# homescreen/serve.py
"""Always-on daemon. Serves from cache only -- performs no network I/O."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, request

from homescreen.cache import read_cache
from homescreen.config import (device, feed_cache_path, feed_config,
                               load_config, server_config)

log = logging.getLogger(__name__)

# ADDENDUM §6: "Each entry declares its render mode; serve.py routes on it."
# Routing on `render` rather than `kind` keeps that field live config -- a new
# data-push device class then declares itself instead of needing a Python edit.
DATA_RENDER = "device"

# ADDENDUM §6's `source` is an acquisition MODE (api | dump1090); PLAN.md §3's
# `feed.source` is the PROVIDER name in a diagnostic response. Map, don't pipe:
# "api" is never a provider, and tells a debugger nothing.
SOURCE_LABEL = {"api": "adsb.fi", "dump1090": "dump1090"}

# kExtrapolationHorizonSec in the firmware (adsb_client.h). Past this the device
# treats a fix as unusable, so past this we must stop serving 304s.
STALE_HORIZON_S = 12.0


def _cache_epoch(env: dict) -> float:
    """Epoch seconds of the cached data's fetched_at."""
    return datetime.fromisoformat(env["fetched_at"]).timestamp()


def _feed_state(env: dict | None, now: float) -> tuple[bool, float]:
    """(ok, age_s). Degrades to (False, 0.0) on anything unreadable."""
    if env is None:
        return False, 0.0
    try:
        delta = now - _cache_epoch(env)
    except (KeyError, TypeError, ValueError):
        return False, 0.0
    if delta < -1.0:
        # Stamp is in our future. A Pi 4 has no RTC: it boots at the time
        # timesyncd last saved and jumps when NTP lands, so pre-sync stamps sit
        # ahead of us. Clamping to 0.0 would report a perfectly fresh feed over
        # arbitrarily old data -- the exact inverse of SPEC §11.4.
        return False, 0.0
    return bool(env["ok"]), max(0.0, delta)


def resolve_version(root: Path) -> str:
    """Short git SHA of the deployed tree, or "unknown".

    Resolved once at startup, never per request: a status page that shells out
    on every hit is a liability, and the answer cannot change under a running
    process anyway. A tarball deploy or a Pi without git degrades rather than
    stopping the server.
    """
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5)
        sha = out.stdout.strip()
        if out.returncode != 0 or not sha:
            return "unknown"
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                               capture_output=True, text=True, timeout=5).stdout
        return f"{sha}-dirty" if dirty.strip() else sha
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _device_summary(cfg: dict, dev: dict, cache_dir: Path, now: float,
                    telemetry: dict) -> dict:
    """Structural facts about one device. Never includes a config VALUE that
    could be a secret -- only keys we name explicitly."""
    is_data = dev.get("render") == DATA_RENDER
    feed = None
    if is_data:
        env = read_cache(feed_cache_path(cache_dir, dev))
        ok, dwell = _feed_state(env, now)
        feed = {"ok": ok, "age_s": round(dwell, 1),
                "aircraft": len(_servable(env, dwell)),
                "fetched_at": (env or {}).get("fetched_at"),
                "error": (env or {}).get("error")}
    dev_id = dev["id"]
    return {
        "id": dev_id,
        "kind": dev.get("kind"),
        "render": dev.get("render"),
        "feed_name": dev.get("feed"),
        "poll_seconds": dev.get("poll_seconds"),
        "feed": feed,
        "endpoints": {
            "data": f"/api/display/{dev_id}/data" if is_data else None,
            "health": f"/api/display/{dev_id}/health",
        },
        "last_telemetry": telemetry.get(dev_id),
    }


def _duration(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


_HOME_CSS = """
:root{--bg:#fff;--fg:#111;--dim:#666;--line:#e3e3e3;--ok:#0a7d33;--bad:#b3261e;
      --card:#fafafa;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#151516;--fg:#e8e8e8;--dim:#9a9a9a;
      --line:#2c2c2e;--ok:#5ddb84;--bad:#ff6b5e;--card:#1d1d1f}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem;background:var(--bg);color:var(--fg);
     font:15px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:56rem;margin:0 auto}
h1{font-size:1.35rem;margin:0 0 .15rem;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:.85rem;margin-bottom:1.75rem}
.sub code{font-family:var(--mono)}
h2{font-size:.72rem;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
   margin:2rem 0 .6rem;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;
      padding:.9rem 1.1rem;margin-bottom:.7rem}
.row{display:flex;flex-wrap:wrap;gap:.5rem 1.5rem;align-items:baseline}
.name{font-weight:600}
.tag{font-size:.7rem;color:var(--dim);border:1px solid var(--line);
     border-radius:99px;padding:.05rem .5rem}
.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
dl{display:grid;grid-template-columns:auto 1fr;gap:.3rem 1rem;margin:.75rem 0 0;
   font-size:.85rem}
dt{color:var(--dim)}dd{margin:0;font-family:var(--mono);word-break:break-all}
a{color:inherit}
footer{margin-top:2.5rem;color:var(--dim);font-size:.78rem}
"""


def _render_home(st: dict) -> str:
    e = html.escape

    def esc(v):
        return e(str(v)) if v is not None else "&mdash;"

    cards = []
    for d in st["devices"]:
        f = d["feed"]
        if f is None:
            state = '<span class="tag">no feed &mdash; pixel push, Phase C</span>'
            detail = ""
        elif f["fetched_at"] is None:
            state = '<span class="bad">never fetched</span>'
            detail = ""
        else:
            state = (f'<span class="ok">healthy</span>' if f["ok"]
                     else f'<span class="bad">stale &mdash; {e(str(f["error"]))}</span>')
            detail = (f'<dt>aircraft</dt><dd>{f["aircraft"]}</dd>'
                      f'<dt>feed age</dt><dd>{f["age_s"]}s</dd>'
                      f'<dt>last fetch</dt><dd>{esc(f["fetched_at"])}</dd>')
        links = "".join(
            f'<dt>{k}</dt><dd><a href="{e(v)}">{e(v)}</a></dd>'
            for k, v in d["endpoints"].items() if v)
        tel = d["last_telemetry"]
        tel_row = ("<dt>last telemetry</dt><dd>"
                   + e(", ".join(f"{k}={v}" for k, v in tel.items())) + "</dd>"
                   ) if tel else ""
        cards.append(f"""<div class="card">
  <div class="row"><span class="name">{esc(d["id"])}</span>
    <span class="tag">{esc(d["kind"])}</span>
    <span class="tag">render: {esc(d["render"])}</span>
    <span class="tag">poll {esc(d["poll_seconds"])}s</span>
    {state}</div>
  <dl>{detail}{links}{tel_row}</dl>
</div>""")

    feed = st["feed"]
    return f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HomeScreen &mdash; {esc(st["version"])}</title>
<style>{_HOME_CSS}</style>
<main>
  <h1>HomeScreen display backend</h1>
  <div class="sub">version <code>{esc(st["version"])}</code>
    &middot; up {e(_duration(st["uptime_s"]))}
    &middot; {len(st["devices"])} device(s) registered</div>

  <h2>Upstream feed</h2>
  <div class="card"><dl>
    <dt>provider</dt><dd>{esc(feed["source"])}</dd>
    <dt>endpoint</dt><dd>{esc(feed["endpoint"])}</dd>
    <dt>fetch every</dt><dd>{esc(feed["fetch_seconds"])}s</dd>
  </dl></div>

  <h2>Devices</h2>
  {"".join(cards) or '<div class="card">none registered</div>'}

  <footer>Machine-readable: <a href="/api/status">/api/status</a>.
  Config structure only &mdash; no secrets are rendered here.</footer>
</main>"""


def _source_label(cfg: dict) -> str:
    mode = feed_config(cfg).get("source", "api")
    if not isinstance(mode, str):
        # A list/dict here would make .get() raise TypeError: unhashable, and
        # 500 every request. Every other lookup in this path is guarded.
        return "unknown"
    return SOURCE_LABEL.get(mode, mode)


def _aircraft(env: dict | None) -> list:
    """The cached aircraft list, or [] for any other shape.

    `read_cache` guarantees `data` is a dict; it does not guarantee what is
    inside it. `{"aircraft": 5}` would otherwise raise straight into a 500.
    """
    raw = env["data"].get("aircraft") if env else None
    return raw if isinstance(raw, list) else []


def _servable(env: dict | None, dwell: float) -> list:
    """The aircraft /data actually serves. /health reports len() of THIS, not of
    the raw list, or the debug endpoint misleads exactly when the cache is bad."""
    out = []
    for a in _aircraft(env):
        if not isinstance(a, dict):
            continue
        try:
            age = float(a.get("age", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(age):
            continue
        out.append({**a, "age": age + dwell})
    return out


def create_app(cfg: dict, cache_dir: Path, *, clock=time.time,
               version: str | None = None) -> Flask:
    app = Flask(__name__)
    telemetry: dict[str, dict] = {}
    started_at = clock()
    if version is None:
        version = resolve_version(cache_dir.parent)

    def _lookup(device_id: str, *, require_data_render: bool = True):
        """`/data` is data-push only; `/health` is defined for every device
        (ADDENDUM §5: "server-side status, for debugging")."""
        dev = device(cfg, device_id)
        if dev is None:
            return None
        if require_data_render and dev.get("render") != DATA_RENDER:
            return None
        return dev

    @app.get("/api/display/<device_id>/data")
    def data(device_id: str):
        dev = _lookup(device_id)
        if dev is None:
            return jsonify({"error": "unknown device"}), 404

        if request.args:
            telemetry[device_id] = {**request.args.to_dict(),
                                    "at": int(clock())}
            log.debug("telemetry %s %s", device_id, telemetry[device_id])

        now = clock()
        env = read_cache(feed_cache_path(cache_dir, dev))
        ok, dwell = _feed_state(env, now)

        # Fix age + time the record sat in our cache. Without the dwell term
        # the device under-extrapolates by exactly that much (VALIDATION F4).
        aircraft = _servable(env, dwell)

        body = {
            "server_time": int(now),
            "feed": {"ok": ok, "age_s": dwell,
                     "source": _source_label(cfg)},
            "aircraft": aircraft,
        }

        # ETag hashes the CACHED CONTENT -- not the rendered body (server_time
        # and the dwell-adjusted ages change every second, so that can never
        # 304), and not `fetched_at` (write_cache restamps on every fetch, so
        # that ALSO never 304s while healthy -- and worse, goes stable exactly
        # when the fetcher dies, blinding the device to the stall).
        if env is None:
            ident = "empty"
        else:
            ident = (json.dumps(env["data"], sort_keys=True, separators=(",", ":"))
                     + "|" + str(env["ok"]))
        etag = '"%s"' % hashlib.sha256(ident.encode()).hexdigest()[:16]

        # Never 304 a stale feed: a 304 has no body, so feed.age_s could not
        # reach the device, and feed.age_s is its ONLY stall signal once its own
        # fetch succeeds against us regardless of upstream (VALIDATION F4 #3).
        fresh = ok and dwell < STALE_HORIZON_S
        if fresh and request.headers.get("If-None-Match") == etag:
            resp = Response(status=304)
        else:
            resp = jsonify(body)
        resp.headers["ETag"] = etag
        # int(): a dangling `poll_seconds:` is a present-but-null key, and
        # `.get(k, 5)` does not fall back for it -- the header would read
        # "None". check_config rejects that at startup; this is belt-and-braces
        # for a config edited under a running daemon.
        try:
            poll = int(dev.get("poll_seconds") or 5)
        except (TypeError, ValueError):
            poll = 5
        resp.headers["X-Poll-Seconds"] = str(poll)
        # Set on BOTH branches: headers survive a 304, so a device that does get
        # one still learns the feed's liveness without a body.
        resp.headers["X-Feed-Age"] = f"{dwell:.1f}"
        resp.headers["X-Feed-Ok"] = "1" if ok else "0"
        return resp

    @app.get("/api/display/<device_id>/health")
    def health(device_id: str):
        dev = _lookup(device_id, require_data_render=False)
        if dev is None:
            return jsonify({"error": "unknown device"}), 404
        now = clock()
        body = {
            "device": device_id,
            "render": dev.get("render"),
            "server_time": int(now),
            "last_telemetry": telemetry.get(device_id),
        }
        if dev.get("render") == DATA_RENDER:
            env = read_cache(feed_cache_path(cache_dir, dev))
            ok, dwell = _feed_state(env, now)
            body["feed"] = {"ok": ok, "age_s": dwell,
                            "error": (env or {}).get("error"),
                            "fetched_at": (env or {}).get("fetched_at"),
                            "aircraft": len(_servable(env, dwell))}
        else:
            # A pixel-push device has no ADS-B feed. Reporting ok: false for a
            # feed it never had is a misleading answer on the endpoint whose
            # whole purpose is debugging. Its render state lands in Phase C.
            body["feed"] = None
        return jsonify(body)

    @app.get("/")
    @app.get("/home")
    def home():
        return Response(_render_home(_status()), mimetype="text/html")

    @app.get("/api/status")
    def status():
        return jsonify(_status())

    def _status() -> dict:
        now = clock()
        feed = feed_config(cfg)
        return {
            "service": "homescreen",
            "version": version,
            "server_time": int(now),
            "uptime_s": round(now - started_at, 2),
            "feed": {
                # Named keys only -- never the whole feed dict, which may hold
                # an api_key (SPEC §7.4) on an unauthenticated LAN endpoint.
                "source": _source_label(cfg),
                "endpoint": feed.get("endpoint"),
                "fetch_seconds": feed.get("fetch_seconds"),
            },
            "devices": [_device_summary(cfg, d, cache_dir, now, telemetry)
                        for d in (cfg.get("devices") or [])
                        if isinstance(d, dict) and d.get("id")],
        }

    return app


def startup(root: Path) -> tuple[Flask, str, int]:
    """Load config and build the app. Raises SystemExit(78) on a config fault.

    Extracted from main() so the guard is testable -- see the fetch daemon's
    startup() for why. `server:` with its children commented out is a
    present-but-null key, so `cfg.get("server", {})` returns None.
    """
    try:
        cfg = load_config(root / "config.yaml")
        srv = server_config(cfg)
        host = str(srv.get("host", "0.0.0.0"))
        port = int(srv.get("port", 8080))
        return create_app(cfg, root / "cache",
                          version=resolve_version(root)), host, port
    except Exception as exc:  # noqa: BLE001
        log.exception("bad config: %s", exc)
        raise SystemExit(78) from None   # EX_CONFIG; see RestartPreventExitStatus


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Werkzeug logs one access line per request: at poll_seconds=5 that is
    # ~17k journal lines/day per device onto a microSD whose wear CLAUDE.md §2
    # flags as unmitigated. Task 6 also caps journald.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app, host, port = startup(Path(__file__).resolve().parents[1])
    # Werkzeug's dev server. Adequate for a handful of LAN devices; swap for
    # waitress in Phase C when N clients start pulling 48 KB frames.
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
