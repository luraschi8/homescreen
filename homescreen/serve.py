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

from homescreen import overrides, registry, scenes, web
from homescreen import render
from homescreen.render import (RenderBusy, RenderError, check_geometry as
                               render_check_geometry, render_frame)
from homescreen.cache import read_cache
from homescreen.config import (check_device, device, feed_cache_path,
                               feed_config, load_config, server_config)

log = logging.getLogger(__name__)

# ADDENDUM §6: "Each entry declares its render mode; serve.py routes on it."
# Routing on `render` rather than `kind` keeps that field live config -- a new
# data-push device class then declares itself instead of needing a Python edit.
DATA_RENDER = "device"

#: How much age error a 304 is allowed to hide, in seconds. The device
#: extrapolates against a 12s horizon (`kExtrapolationHorizonSec`), so this is
#: a sixth of it: small enough that the dead-reckoned position stays honest,
#: large enough that a healthy feed still 304s most of the time.
AGE_BUCKET_S = 2.0

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
        """Resolve a friendly name to a device.

        `/data` is data-push only; `/health` answers for every device
        (ADDENDUM §5: "server-side status, for debugging").

        Config is tried FIRST so the live /api/display/radar/data route cannot
        regress while config and registry devices coexist. A registry device is
        reachable by the name a human gave it -- without this the fleet view
        lists devices you cannot curl (ADDENDUM §4.5).
        """
        dev = device(_live(), device_id)
        if dev is None:
            hw = registry.resolve_name(cache_dir, device_id)
            if hw is None:
                return None
            rec = registry.load(cache_dir).get(hw) or {}
            dev = {"id": device_id, "hw": hw, "feed": "adsb",
                   "render": DATA_RENDER,
                   "poll_seconds": rec.get("poll_seconds")}
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
        #
        # ...but the content alone is not the whole identity. Every aircraft
        # age carries the dwell term, and a body-less 304 leaves the device
        # holding the ages from the LAST body it received. Measured: a device
        # that 304s for 11.9s still believed its fix was 1.0s old -- the fix
        # was 12.9s old, ~3 km at 250 m/s, and because `pos_age_s` stayed
        # frozen the firmware's own dimming test never fired either. That is
        # VALIDATION F4 returning through the one door it did not cover.
        #
        # So the dwell BUCKET is part of the identity. A 304 can now hide at
        # most AGE_BUCKET_S of age error rather than the whole horizon, while
        # a feed that is genuinely updating still 304s for free -- its dwell
        # resets on every fetch and never leaves the first bucket.
        if env is None:
            ident = "empty"
        else:
            ident = (json.dumps(env["data"], sort_keys=True, separators=(",", ":"))
                     + "|" + str(env["ok"])
                     + "|" + str(int(dwell // AGE_BUCKET_S)))
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

        try:
            overrides.save(cache_dir, candidate)
        except OSError as exc:
            log.error("override write failed for %s: %s", device_id, exc)
            return jsonify({"error": "config store unavailable"}), 503
        log.info("config patched: %s %s", device_id, body)
        return jsonify({"id": device_id,
                        "overridden": candidate[device_id],
                        "effective": {k: _dotted(device(probe, device_id), k)
                                      for k in overrides.SETTABLE_KEYS},
                        "note": "the fetch daemon picks this up next cycle"})

    @app.delete("/api/config/devices/<device_id>")
    def reset_config(device_id: str):
        if device(_live(), device_id) is None:
            return jsonify({"error": "unknown device"}), 404
        data = overrides.load(cache_dir)
        if data.pop(device_id, None) is None:
            # Nothing to revert: do not write to a wear-limited card for it.
            return jsonify({"id": device_id, "overridden": {},
                            "note": "nothing was overridden"})
        try:
            overrides.save(cache_dir, data)
        except OSError as exc:
            log.error("override write failed for %s: %s", device_id, exc)
            return jsonify({"error": "config store unavailable"}), 503
        return jsonify({"id": device_id, "overridden": {},
                        "note": "reverted to config.yaml"})

    _DEVICE_READONLY = ("fw", "caps", "telemetry", "first_seen", "last_seen")
    #: A cold frame costs ~2.9s of Chromium on the Pi against 2 render slots.
    #: A device polling on cadence needs at most one per interval; anything
    #: faster is either a bug or a stranger, and both are answered instantly
    #: instead of being allowed to occupy a slot.
    _last_cold: dict[str, float] = {}

    #: A per-hw throttle alone is defeated by minting hardware ids: 40 invented
    #: ids buy 40 cold renders. So an UNCONFIGURED device -- one no operator has
    #: ever assigned a scene to -- also draws on a single global budget. That is
    #: exactly the attacker's shape, and it costs a real newly-flashed panel at
    #: most a few seconds of waiting on its very first boot. A configured device
    #: is never gated by this: the fleet is bounded at MAX_DEVICES and every one
    #: of them is entitled to the frames it polls for.
    UNCONFIGURED_COLD_INTERVAL_S = 1.0
    _last_unconfigured = [0.0]

    def _cold_render_allowed(hw: str, rec: dict) -> bool:
        interval = registry.poll_seconds(rec) * 0.5
        now = time.monotonic()
        last = _last_cold.get(hw)
        if last is not None and now - last < interval:
            return False
        if rec.get("scene") in (None, "", "unassigned"):
            if now - _last_unconfigured[0] < UNCONFIGURED_COLD_INTERVAL_S:
                return False
            _last_unconfigured[0] = now
        _last_cold[hw] = now
        if len(_last_cold) > registry.MAX_DEVICES * 2:
            for key in sorted(_last_cold, key=_last_cold.get)[:registry.MAX_DEVICES]:
                _last_cold.pop(key, None)
        return True

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
        except OSError as exc:
            # ext4 remounts read-only on error and CLAUDE.md flags SD wear as
            # live. The device path already 503s here; the admin path must too.
            log.error("registry write failed for %s: %s", hw, exc)
            return jsonify({"error": "registry unavailable"}), 503
        # An operator just changed what this panel shows. That deliberately
        # costs one render: without this, switching a scene in the fleet view
        # leaves an e-paper on the old one for up to half a poll interval.
        _last_cold.pop(hw, None)
        return jsonify(_fleet_entry(hw, rec, clock()))

    @app.delete("/api/devices/<hw>")
    def delete_device(hw: str):
        try:
            if not registry.forget(cache_dir, hw):
                return jsonify({"error": "unknown device"}), 404
        except OSError as exc:
            log.error("registry write failed for %s: %s", hw, exc)
            return jsonify({"error": "registry unavailable"}), 503
        return jsonify({"hw": hw, "forgotten": True})

    def _caps_from_query(args) -> dict:
        """Capabilities ride on every call rather than a separate handshake, so
        a server restart cannot lose them and there is no registration state
        machine to get wrong. registry.clean_caps range-checks them.

        A LIST capability is only read when the same request also declares
        geometry. Without that rule `?components=nothing` is a complete,
        one-GET declaration in its own right: it merges over the stored caps
        and blanks the radar until the device next speaks. Requiring `w` and
        `h` alongside it means a fragment cannot redefine a device -- an
        attacker must impersonate the whole handshake, and the device's own
        next poll overwrites it.
        """
        caps = {}
        for key in _CAP_INTS:
            if key in args:
                caps[key] = args[key]
        if "w" not in caps or "h" not in caps:
            return caps
        for key in _CAP_LISTS:
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip():
                caps[key] = [p for p in (x.strip() for x in raw.split(",")) if p]
        return caps

    def _register(hw: str, *, declare: bool = True):
        """Shared by every device-facing route. Returns (record, error_response).

        `declare=False` records that we heard from the device without letting
        the request redefine what the device IS. Only the scene handshake
        declares capabilities; see `device_frame` for why the frame route must
        not.
        """
        args = request.args.to_dict()
        caps = _caps_from_query(args) if declare else {}
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

    def _poll_seconds(rec: dict) -> int:
        return registry.poll_seconds(rec)

    def _poll_header(resp, rec):
        resp.headers["X-Poll-Seconds"] = str(_poll_seconds(rec))
        return resp

    def _scene_for(hw: str, rec: dict):
        """(scene_name, Scene). An unassigned device gets a real scene telling
        a human what to do, never an error and never a blank."""
        name = rec.get("scene") or "unassigned"
        ctx = scenes.SceneContext(
            cfg=_live(), cache_dir=cache_dir,
            # Sanitised once here: a hand-edited devices.json could otherwise
            # put a string in `w`, and every scene does int(caps["w"]).
            caps=registry.clean_caps(rec.get("caps") or {}),
            now=clock(),
            device={"hw": hw, "id": rec.get("name") or hw,
                    "name": rec.get("name"), "feed": "adsb",
                    "max_aircraft": (device(_live(), rec.get("name") or "") or {})
                                    .get("max_aircraft", 20)})
        if name in ("unassigned", "error"):
            return name, scenes.safe_build("status", ctx)
        return name, scenes.safe_build(name, ctx)

    @app.get("/api/device/<hw>/scene")
    def device_scene(hw: str):
        rec, err = _register(hw)
        if err:
            return err
        name, scene = _scene_for(hw, rec)
        assigned = name not in ("unassigned", "error")
        # ADDENDUM §5.5: a device never receives a component it did not
        # declare, so it needs no error path for one. The substitution is
        # reported rather than silent.
        declared = (registry.clean_caps(rec.get("caps") or {})
                    .get("components"))
        kept, dropped = list(scene.components), []
        if declared:
            kept = [c for c in scene.components if c.get("c") in declared]
            dropped = [c.get("c") for c in scene.components
                       if c.get("c") not in declared]
        body = {"hw": hw, "name": rec.get("name"), "scene": name,
                "assigned": assigned, "layout": scene.layout,
                "components": kept}
        if dropped:
            body["unsupported"] = sorted(set(dropped))
        if not assigned:
            body["message"] = "sin asignar · elige una escena en el panel"
        return _poll_header(jsonify(body), rec)

    def _agreed_geometry(rec: dict, args: dict):
        """(w, h, error_response). The one number a device cannot check.

        A device streams the body straight at the panel, so a wrong-length body
        at HTTP 200 is a corrupt screen rather than an error it can detect.
        The rule is therefore: the request SAYS what it expects, the server
        AGREES or refuses. Disagreement is a 409, never a silent substitution.

        This replaces "merge the query over the stored record", which read as a
        defence and was not one: `_register` had already merged the query INTO
        the record, so both sides of the merge were the attacker's. One
        unauthenticated GET re-geometried someone else's panel and the next
        poll got 7,200 bytes at HTTP 200 for 800x480 glass.
        """
        stored = registry.clean_caps(rec.get("caps") or {})
        asked = registry.clean_caps({k: v for k, v in args.items()
                                     if k in ("w", "h")})
        sw, sh = stored.get("w"), stored.get("h")
        aw, ah = asked.get("w"), asked.get("h")
        if aw is None and ah is None:
            return int(sw or 800), int(sh or 480), None
        w, h = int(aw or sw or 800), int(ah or sh or 480)
        if sw is not None and sh is not None and (w, h) != (int(sw), int(sh)):
            return 0, 0, (jsonify({
                "error": "geometry disagrees with this device's handshake",
                "requested": {"w": w, "h": h},
                "registered": {"w": int(sw), "h": int(sh)},
                "hint": "re-declare capabilities on /scene, then retry"}), 409)
        return w, h, None

    @app.get("/api/device/<hw>/frame")
    def device_frame(hw: str):
        """Packed 1bpp, MSB first, 1 = black. No header, no compression.

        The device streams this straight at the panel, so anything other than
        exactly w*h/8 bytes is a corrupt screen rather than an error it can
        detect. We would rather 503 than serve a short frame.
        """
        rec, err = _register(hw, declare=False)
        if err:
            return err
        w, h, err = _agreed_geometry(rec, request.args.to_dict())
        if err:
            return err
        try:
            # Reject before building or rendering: a device declares its own
            # geometry, so this bounds what a device can make the Pi do.
            render_check_geometry(w, h)
        except RenderError as exc:
            return jsonify({"error": str(exc)}), 400
        name, scene = _scene_for(hw, rec)
        if not scene.html:
            return jsonify({"error": f"scene {name!r} has no pixel rendering"}), 409
        if not render.is_cached(scene.html, w, h) \
                and not _cold_render_allowed(hw, rec):
            resp = jsonify({"error": "frame requested faster than this device "
                                     "polls; the render queue is not a toy"})
            resp.headers["Retry-After"] = "5"
            return resp, 429
        try:
            packed = render_frame(scene.html, w, h)
        except RenderBusy as exc:
            resp = jsonify({"error": str(exc)})
            resp.headers["Retry-After"] = "5"
            return resp, 503
        except RenderError as exc:
            # Chromium missing or a render timeout must not look like a frame.
            log.error("frame render failed for %s: %s", hw, exc)
            return jsonify({"error": f"render failed: {exc}"}), 503
        etag = '"%s"' % hashlib.sha256(packed).hexdigest()[:16]
        if request.headers.get("If-None-Match") == etag:
            resp = Response(status=304)
        else:
            resp = Response(packed, mimetype="application/octet-stream")
        resp.headers["ETag"] = etag
        resp.headers["X-Frame-Bytes"] = str(len(packed))
        resp.headers["X-Scene"] = name
        return _poll_header(resp, rec)

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
