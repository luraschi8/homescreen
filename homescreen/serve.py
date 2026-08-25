# homescreen/serve.py
"""Always-on daemon. Serves from cache only -- performs no network I/O."""

from __future__ import annotations

import hashlib
import json
import logging
import math
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


def create_app(cfg: dict, cache_dir: Path, *, clock=time.time) -> Flask:
    app = Flask(__name__)
    telemetry: dict[str, dict] = {}

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
        return create_app(cfg, root / "cache"), host, port
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
