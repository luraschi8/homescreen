# homescreen/serve.py
"""Always-on daemon. Serves from cache only -- performs no network I/O."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, request

from homescreen import overrides, registry, web
from homescreen.cache import read_cache
from homescreen.config import (check_device, device, feed_cache_path,
                               feed_config, load_config, server_config)

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


def _dotted(node: dict, dotted: str):
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


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
    try:
        registry.seed_from_config(cfg, cache_dir)
    except Exception:  # noqa: BLE001
        # startup() wraps create_app in `except Exception -> SystemExit(78)`,
        # and the unit sets RestartPreventExitStatus=78. An unwritable cache
        # dir would therefore PERMANENTLY stop the daemon. The fleet view
        # degrades; the service does not die.
        log.exception("could not seed the registry from config")
    if version is None:
        version = resolve_version(cache_dir.parent)

    def _lookup(device_id: str, *, require_data_render: bool = True):
        cfg_live = _live()
        """`/data` is data-push only; `/health` is defined for every device
        (ADDENDUM §5: "server-side status, for debugging")."""
        dev = device(cfg_live, device_id)
        if dev is None:
            return None
        if require_data_render and dev.get("render") != DATA_RENDER:
            return None
        return dev

    def _live() -> dict:
        """cfg with the runtime overlay applied. Re-read per request so a PATCH
        takes effect immediately without restarting the daemon."""
        return overrides.apply(cfg, cache_dir)

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
        return Response(web.render_home(_status()), mimetype="text/html")

    @app.get("/api/config")
    def get_config():
        live = _live()
        data = overrides.load(cache_dir)
        return jsonify({
            "settable": list(overrides.SETTABLE_KEYS),
            "devices": [
                {"id": d["id"],
                 "effective": {k: _dotted(d, k) for k in overrides.SETTABLE_KEYS},
                 "overridden": data.get(d["id"], {})}
                for d in (live.get("devices") or [])
                if isinstance(d, dict) and d.get("id")],
        })

    @app.patch("/api/config/devices/<device_id>")
    def patch_config(device_id: str):
        live = _live()
        if device(live, device_id) is None:
            return jsonify({"error": "unknown device"}), 404
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not body:
            return jsonify({"error": "body must be a non-empty JSON object"}), 400

        bad = [k for k in body if k not in overrides.SETTABLE_KEYS]
        if bad:
            return jsonify({"error": f"not settable: {sorted(bad)}",
                            "settable": list(overrides.SETTABLE_KEYS)}), 400

        # Validate the RESULT before persisting. This file is written from the
        # network and re-read by the fetch daemon, so an unvalidated write is a
        # remote way to wedge the service. Nothing lands on disk unless the
        # config it produces would survive startup.
        data = overrides.load(cache_dir)
        candidate = {**data, device_id: {**data.get(device_id, {}), **body}}
        probe = overrides.apply(cfg, cache_dir, _data=candidate)
        try:
            check_device(device(probe, device_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        overrides.save(cache_dir, candidate)
        log.info("config patched: %s %s", device_id, body)
        return jsonify({"id": device_id,
                        "overridden": candidate[device_id],
                        "effective": {k: _dotted(device(probe, device_id), k)
                                      for k in overrides.SETTABLE_KEYS},
                        "note": "the fetch daemon picks this up next cycle"})

    @app.delete("/api/config/devices/<device_id>")
    def reset_config(device_id: str):
        data = overrides.load(cache_dir)
        data.pop(device_id, None)
        overrides.save(cache_dir, data)
        return jsonify({"id": device_id, "overridden": {},
                        "note": "reverted to config.yaml"})

    _DEVICE_READONLY = ("fw", "caps", "telemetry", "first_seen", "last_seen")
    _SETTABLE = ("name", "scene", "poll_seconds")
    _CAP_INTS = ("w", "h", "depth")
    _CAP_LISTS = ("layouts", "components")

    def _fleet_entry(hw: str, rec: dict, now: float) -> dict:
        return {"hw": hw, "name": rec.get("name"), "scene": rec.get("scene"),
                "fw": rec.get("fw"), "poll_seconds": rec.get("poll_seconds"),
                "online": registry.is_online(rec, now),
                "last_seen": rec.get("last_seen"),
                "first_seen": rec.get("first_seen"),
                "caps": rec.get("caps", {}) or {},
                "telemetry": rec.get("telemetry", {}) or {}}

    @app.get("/api/devices")
    def list_devices():
        now = clock()
        return jsonify({
            "scenes": list(registry.ASSIGNABLE_SCENES),
            "devices": [_fleet_entry(hw, rec, now)
                        for hw, rec in sorted(registry.load(cache_dir).items())],
        })

    @app.get("/api/devices/<hw>")
    def get_device(hw: str):
        rec = registry.load(cache_dir).get(hw)
        if rec is None:
            return jsonify({"error": "unknown device"}), 404
        return jsonify(_fleet_entry(hw, rec, clock()))

    @app.patch("/api/devices/<hw>")
    def patch_device(hw: str):
        if hw not in registry.load(cache_dir):
            return jsonify({"error": "unknown device"}), 404
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not body:
            return jsonify({"error": "body must be a non-empty JSON object"}), 400
        readonly = sorted(k for k in body if k in _DEVICE_READONLY)
        if readonly:
            return jsonify({"error": f"device-reported, not settable: {readonly}"}), 400
        unknown = sorted(k for k in body if k not in _SETTABLE)
        if unknown:
            return jsonify({"error": f"not settable: {unknown}",
                            "settable": list(_SETTABLE)}), 400
        try:
            rec = registry.assign(cache_dir, hw, **body)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_fleet_entry(hw, rec, clock()))

    @app.delete("/api/devices/<hw>")
    def delete_device(hw: str):
        if not registry.forget(cache_dir, hw):
            return jsonify({"error": "unknown device"}), 404
        return jsonify({"hw": hw, "forgotten": True})

    def _caps_from_query(args) -> dict:
        """Capabilities ride on every call rather than a separate handshake, so
        a server restart cannot lose them and there is no registration state
        machine to get wrong. registry.clean_caps range-checks them."""
        caps = {}
        for key in _CAP_INTS:
            if key in args:
                caps[key] = args[key]
            
        for key in _CAP_LISTS:
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip():
                caps[key] = [p for p in (x.strip() for x in raw.split(",")) if p]
        return caps

    def _register(hw: str):
        """Shared by every device-facing route. Returns (record, error_response)."""
        args = request.args.to_dict()
        caps = _caps_from_query(args)
        telemetry = {k: v for k, v in args.items()
                     if k not in _CAP_INTS + _CAP_LISTS + ("fw",)}
        try:
            rec = registry.touch(cache_dir, hw, fw=args.get("fw"),
                                 caps=caps or None, telemetry=telemetry or None,
                                 now=clock())
        except ValueError as exc:
            return None, (jsonify({"error": str(exc)}), 400)
        except OSError as exc:
            # The registry lives on a microSD. A write failure must not take
            # the device's screen down with it.
            log.error("registry write failed for %s: %s", hw, exc)
            return None, (jsonify({"error": "registry unavailable"}), 503)
        return rec, None

    def _poll_header(resp, rec):
        try:
            poll = int(float(rec.get("poll_seconds") or registry.DEFAULT_POLL_SECONDS))
        except (TypeError, ValueError):
            poll = registry.DEFAULT_POLL_SECONDS
        resp.headers["X-Poll-Seconds"] = str(poll)
        return resp

    @app.get("/api/device/<hw>/scene")
    def device_scene(hw: str):
        rec, err = _register(hw)
        if err:
            return err
        assigned = rec.get("scene") not in (None, "unassigned")
        body = {"hw": hw, "name": rec.get("name"),
                "scene": rec.get("scene") or "unassigned", "assigned": assigned}
        if not assigned:
            # A newly flashed board should be able to tell you what to type
            # into the fleet view, rather than sitting blank.
            body["message"] = "not assigned - set a scene in the fleet view"
        return _poll_header(jsonify(body), rec)

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
            "fleet": [_fleet_entry(hw, rec, now)
                      for hw, rec in sorted(registry.load(cache_dir).items())],
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
