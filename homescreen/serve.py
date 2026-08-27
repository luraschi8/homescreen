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

from flask import (Flask, Response, jsonify, redirect, request,
                   url_for)

from homescreen import draw, overrides, registry, scenes, web
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


def _scene_identity(body: dict) -> dict:
    """The scene's identity for ETag purposes: the body with its clocks
    quantised to AGE_BUCKET_S.

    Every time-derived number in a scene advances on every request, so hashing
    the body verbatim makes each response unique and the 304 unreachable. These
    are the only fields that move without the picture changing, so bucketing
    exactly them -- and nothing else -- keeps the ETag honest about content
    while letting an unchanged sky answer 304.
    """
    out = {k: v for k, v in body.items() if k != "components"}
    components = []
    for comp in body.get("components") or ():
        if not isinstance(comp, dict):
            components.append(comp)
            continue
        c = dict(comp)
        if isinstance(c.get("feed_age_s"), (int, float)):
            c["feed_age_s"] = int(c["feed_age_s"] // AGE_BUCKET_S)
        items = []
        for item in c.get("items") or ():
            if isinstance(item, dict) and isinstance(item.get("age"),
                                                     (int, float)):
                item = {**item, "age": int(item["age"] // AGE_BUCKET_S)}
            items.append(item)
        if "items" in c:
            c["items"] = items
        components.append(c)
    out["components"] = components
    return out


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
    # A PATCH body has no business being large, and without this Flask has no
    # limit at all: an 8 MB `show_ground` was accepted, fsynced to the
    # wear-limited microSD, and then re-read and parsed on EVERY request by
    # `_live()` and on every cycle by the fetch daemon -- 0.11ms/request became
    # 10.2ms. Bounded here so the parse never happens rather than being made
    # cheap.
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
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
                   # The DERIVED cadence, not the raw field. The raw field is
                   # None until an operator picks one, and `or 5` on a None
                   # told a 1-bit panel 5 while /scene, /frame and the fleet
                   # view all said 30. One device, two answers, and the
                   # firmware decides which -- that is how an e-paper ends up
                   # refreshing every 5 seconds.
                   "poll_seconds": registry.advertised_poll_seconds(rec),
                   "caps": rec.get("caps") or {}}
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
            telemetry[device_id] = {**registry.bound_strings(
                request.args.to_dict(), registry.MAX_TELEMETRY_KEYS),
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
        # One cadence, one function. This route computed its own, so a
        # self-registered device -- whose stored poll_seconds is None until an
        # operator chooses one -- was told 30 by /scene, /frame and
        # /api/devices, and 5 here, while the fleet view judged it against 90.
        resp.headers["X-Poll-Seconds"] = str(registry.poll_seconds(dev))
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
        return Response(
            web.render_home(_status(),
                            scene_options={
                                hw: _scene_options(rec) for hw, rec
                                in registry.load(cache_dir).items()},
                            name_max=registry.NAME_MAX,
                            notice=request.args.get("m", "")),
            mimetype="text/html")

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
    #: A cold frame costs ~2.9s of Chromium on the Pi against 2 render slots,
    #: so the queue has to be rationed. The question is what to ration it BY.
    #:
    #: Per hardware id alone fails: ids are free to invent, and 40 invented ids
    #: bought 40 renders. A global budget for unconfigured devices fails worse:
    #: a newly flashed panel is unconfigured too, its first frame is always a
    #: cold render (the unassigned scene embeds its own hw id), and a flood
    #: rotating ids held that budget permanently -- the real panel never got a
    #: first frame at all, which is the opposite of what the budget was for.
    #:
    #: The honest fairness key is the peer address. A flood from one host
    #: spends one host's budget; a real panel is a different host and is
    #: unaffected. Spoofing is possible on a LAN but the spoofer must still
    #: receive the reply, which is a far higher bar than making up a MAC. Both
    #: keys are used: per-peer rations the cost, per-hw stops one device
    #: hammering us regardless of where it sits.
    #: An assigned device is entitled to the frames it polls for -- the fleet
    #: is bounded at MAX_DEVICES and an operator chose every one of them -- so
    #: it is exempt from the peer budget. That matters when a panel and a
    #: hostile process share an address, which peer-keying alone cannot
    #: separate. What still bounds a configured device is COLD_FLOOR_S below.
    #: A stranger CAN assign itself a scene, because the fleet API is
    #: deliberately unauthenticated on a trusted LAN -- see CLAUDE.md. That is
    #: the accepted cost of that decision, not an oversight here.
    COLD_BURST = 4                 # renders an unconfigured peer may take at once
    COLD_REFILL_S = 3.0            # ...and one more every this many seconds
    #: No device, however it is configured, may spend the browser faster than
    #: this. `poll_seconds: 1` otherwise bought a cold render every 0.5s.
    COLD_FLOOR_S = 2.0
    _peer_bucket: dict[str, list] = {}
    _last_cold: dict[str, float] = {}

    def _peer_allows_cold(peer: str) -> bool:
        now = time.monotonic()
        tokens, stamp = _peer_bucket.get(peer, (float(COLD_BURST), now))
        tokens = min(float(COLD_BURST),
                     tokens + (now - stamp) / COLD_REFILL_S)
        if tokens < 1.0:
            _peer_bucket[peer] = (tokens, now)
            return False
        _peer_bucket[peer] = (tokens - 1.0, now)
        if len(_peer_bucket) > 256:
            for key in sorted(_peer_bucket, key=lambda k: _peer_bucket[k][1])[:128]:
                _peer_bucket.pop(key, None)
        return True

    def _cold_render_allowed(hw: str, rec: dict, peer: str) -> bool:
        # Per-hw first: a device polling faster than half its own cadence is
        # either broken or not the device, and either way the answer is cheap.
        interval = max(COLD_FLOOR_S, registry.poll_seconds(rec) * 0.5)
        now = time.monotonic()
        last = _last_cold.get(hw)
        if last is not None and now - last < interval:
            return False
        configured = rec.get("scene") not in (None, "", "unassigned")
        if not configured and not _peer_allows_cold(peer):
            return False
        _last_cold[hw] = now
        if len(_last_cold) > registry.MAX_DEVICES * 2:
            for key in sorted(_last_cold, key=_last_cold.get)[:registry.MAX_DEVICES]:
                _last_cold.pop(key, None)
        return True

    _SETTABLE = ("name", "scene", "poll_seconds", "options")
    _CAP_INTS = registry.CAP_INTS
    _CAP_LISTS = ("layouts", "components")

    #: What the last serve to each device actually did, in memory only. Spec
    #: §5.5 and §6.2 both say the substitution is "recorded in the fleet view";
    #: it reached the DEVICE's own response and nowhere an operator looks. An
    #: operator seeing a panel showing the wrong thing needs to be told the
    #: server dropped something, not left to diff two JSON payloads by hand.
    #: Not persisted: it describes this process's behaviour, not the device.
    _serve_notes: dict[str, dict] = {}

    def _note(hw: str, **fields) -> None:
        if fields:
            _serve_notes[hw] = fields
        else:
            _serve_notes.pop(hw, None)
        if len(_serve_notes) > registry.MAX_DEVICES:
            # Prune by registry membership, not by a size threshold. A
            # threshold was unreachable in any real flow -- a note needs an
            # assigned scene, which needs a registry record -- while the true
            # leak is slower and different in kind: every device that is
            # evicted or forgotten leaves its note behind forever. A note about
            # a device that no longer exists is not a note, it is a leak.
            known = set(registry.load(cache_dir))
            for key in [k for k in _serve_notes if k not in known]:
                _serve_notes.pop(key, None)

    def _scene_options(rec: dict) -> list:
        """(name, renderable, why_not) for every assignable scene, per device.

        The dashboard used to offer every scene to every device. A round display
        picking `clock` got "escena no soportada" on the glass, because clock is
        HTML only and that device draws its own geometry from components. The
        operator had no way to know which choice would work.

        A device that DECLARED components is data-push: it can only show a scene
        that emits one it declared. A device that declared none is pixel-push --
        it takes a rendered framebuffer from /frame, and any scene with html
        works.
        """
        caps = registry.clean_caps(rec.get("caps") or {})
        declared = caps.get("components")
        out = []
        for name in scenes.names():
            try:
                scene = scenes.build(name, scenes.SceneContext(
                    cfg=_live(), cache_dir=cache_dir, caps=caps, now=clock(),
                    options=scenes.defaults(name),
                    device={"hw": "?", "id": rec.get("name") or "?",
                            "name": rec.get("name"), "feed": "adsb"}))
            except Exception:                       # noqa: BLE001
                out.append((name, False, "scene failed to build"))
                continue
            if not declared:
                out.append((name, bool(scene.html), ""
                            if scene.html else "no pixel rendering"))
                continue
            kinds = {c.get("c") for c in scene.components}
            usable = sorted(kinds & set(declared))
            if usable:
                out.append((name, True, ""))
            elif kinds:
                out.append((name, False,
                            f"needs {', '.join(sorted(kinds))}"))
            else:
                out.append((name, False, "no components for this device"))
        return out

    def _fleet_entry(hw: str, rec: dict, now: float) -> dict:
        entry = {"hw": hw, "name": rec.get("name"), "scene": rec.get("scene"),
                 "fw": rec.get("fw"),
                 "poll_seconds": registry.advertised_poll_seconds(rec),
                 "online": registry.is_online(rec, now),
                 "last_seen": rec.get("last_seen"),
                 "first_seen": rec.get("first_seen"),
                 "caps": rec.get("caps", {}) or {},
                 "options": rec.get("options", {}) or {},
                 "option_schema": list(scenes.option_schema(
                     rec.get("scene") or "")),
                 "telemetry": rec.get("telemetry", {}) or {}}
        note = _serve_notes.get(hw)
        if note:
            entry.update(note)
        return entry

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
        if "options" in body:
            # Coerced against the schema of the scene this device will BE on
            # after the patch, not the one it is on now -- otherwise setting a
            # scene and its options in one call validates against the old one.
            target = body.get("scene") or registry.load(cache_dir).get(
                hw, {}).get("scene") or ""
            body = {**body,
                    "options": scenes.clean_options(target, body["options"])}
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

    @app.get("/preview/<hw>/<scene>.svg")
    def preview(hw: str, scene: str):
        """What this scene would look like on THIS device, before assigning it.

        Executes the same instruction list the device would execute. It is not
        the frame -- fonts and antialiasing are the panel's own -- but nothing
        about the layout is guessed, which is the property that makes a preview
        worth showing at all.

        SVG, so a preview cannot fork Chromium or take a render slot: those
        belong to devices asking for frames, and a dashboard refresh must never
        compete with the glass.
        """
        rec = registry.load(cache_dir).get(hw)
        if rec is None:
            return jsonify({"error": "unknown device"}), 404
        if scene not in scenes.names():
            return jsonify({"error": "unknown scene"}), 404
        caps = registry.clean_caps(rec.get("caps") or {})
        w = int(caps.get("w") or 240)
        h = int(caps.get("h") or 240)
        try:
            built = scenes.build(scene, scenes.SceneContext(
                cfg=_live(), cache_dir=cache_dir, caps=caps, now=clock(),
                options=scenes.clean_options(scene, rec.get("options") or {}),
                device={"hw": hw, "id": rec.get("name") or hw,
                        "name": rec.get("name"), "feed": "adsb",
                        "max_aircraft": 20}))
        except Exception as exc:                    # noqa: BLE001
            log.warning("preview %s/%s failed: %s", hw, scene, exc)
            return jsonify({"error": "scene failed to build"}), 503
        instructions = []
        for comp in built.components:
            instructions.extend(comp.get("draw") or ())
        if not instructions:
            # A component with no instruction list -- radar, today -- cannot be
            # previewed exactly, and drawing an approximation would be a
            # different program's guess presented as fact. Say so instead.
            body = draw.to_svg(
                [draw.text("center", scene, "md"),
                 draw.text("below", "sin vista previa", "xs", "dim")],
                w, h, round_panel=(w == h))
        else:
            body = draw.to_svg(instructions, w, h, round_panel=(w == h))
        resp = Response(body, mimetype="image/svg+xml")
        # A preview is cheap to rebuild and always reflects live data.
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/home/device")
    def home_device():
        """The dashboard's form target.

        Form-encoded rather than JSON, and a redirect rather than a body, so the
        page works with no JavaScript at all -- the same constraint the scenes
        live under. It is a thin shell over `registry.assign`, the identical
        call the JSON PATCH makes, so there is exactly one place that decides
        what a valid name or scene is.
        """
        hw = (request.form.get("hw") or "").strip()
        name = (request.form.get("name") or "").strip()
        scene = (request.form.get("scene") or "").strip()
        if hw not in registry.load(cache_dir):
            return redirect(url_for("home", m="unknown device"))
        target = scene or (registry.load(cache_dir).get(hw, {}).get("scene")
                           or "")
        raw_options = {k[4:]: v for k, v in request.form.items()
                       if k.startswith("opt.")}
        # A form that carried ANY option field carried them all -- a text input
        # always submits, and a checkbox that is off submits nothing. So an
        # absent bool means "off", which is what clean_options already does by
        # filling every schema key from its default. A form that carried NO
        # option fields is a rename, and must leave the options alone.
        options = (scenes.clean_options(target, raw_options)
                   if raw_options else None)
        try:
            rec = registry.assign(cache_dir, hw, name=name or None,
                                  scene=scene or None, options=options)
        except ValueError as exc:
            return redirect(url_for("home", m=str(exc)))
        except OSError as exc:
            log.error("registry write failed for %s: %s", hw, exc)
            return redirect(url_for("home", m="registry unavailable"))
        # An operator just changed what this panel shows; that deliberately
        # costs one render rather than waiting out the throttle.
        _last_cold.pop(hw, None)
        shown = rec.get("scene") or "unassigned"
        who = rec.get("name") or hw
        return redirect(url_for("home", m=f"{who} now shows {shown}"))

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

    def _poll_seconds(rec: dict, scene=None) -> int:
        return registry.poll_seconds(
            rec, scene_poll_s=getattr(scene, "poll_s", None))

    def _poll_header(resp, rec, scene=None, hw=None):
        """Tell the device when to come back, and record the ceiling we implied.

        The header may count down to the next change; the recorded budget is the
        stable number the fleet view judges silence against. Both come from one
        place so they can never drift apart -- the bug this shape replaces was
        four routes each deriving a cadence of their own.
        """
        resp.headers["X-Poll-Seconds"] = str(_poll_seconds(rec, scene))
        if hw:
            registry.remember_poll_budget(
                cache_dir, hw,
                registry.poll_budget_seconds(
                    rec, scene_max_s=getattr(scene, "poll_max_s", None)))
        return resp

    def _scene_for(hw: str, rec: dict, caps: dict | None = None):
        """(scene_name, Scene). An unassigned device gets a real scene telling
        a human what to do, never an error and never a blank.

        `caps` overrides the stored record. The frame route passes the caps
        from THIS request, so a scene is never laid out for a geometry a
        stranger wrote into the record.
        """
        name = rec.get("scene") or "unassigned"
        ctx = scenes.SceneContext(
            cfg=_live(), cache_dir=cache_dir,
            # Sanitised once here: a hand-edited devices.json could otherwise
            # put a string in `w`, and every scene does int(caps["w"]).
            caps=registry.clean_caps(caps if caps is not None
                                     else rec.get("caps") or {}),
            options=scenes.clean_options(name, rec.get("options") or {}),
            now=clock(),
            device={"hw": hw, "id": rec.get("name") or hw,
                    "name": rec.get("name"), "feed": "adsb",
                    "max_aircraft": (device(_live(), rec.get("name") or "") or {})
                                    .get("max_aircraft", 20),
                    # What the DEVICE says it can hold. A scene that ignores it
                    # sends a body the device cannot parse: ArduinoJson peaks
                    # around 44 KB at 100 items, against ~55 KB of free heap,
                    # so an operator raising max_aircraft would blank the panel
                    # at exactly the busiest time of day.
                    "max_items": registry.clean_caps(
                        rec.get("caps") or {}).get("max_items")})
        if name in ("unassigned", "error"):
            return name, scenes.without_cadence(scenes.safe_build("status", ctx))
        return name, scenes.safe_build(name, ctx)

    @app.get("/api/device/<hw>/scene")
    def device_scene(hw: str):
        rec, err = _register(hw)
        if err:
            return err
        name, scene = _scene_for(hw, rec)
        scene_error = scene.error
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
        _note(hw, **({"unsupported": sorted(set(dropped))} if dropped else {}),
              **({"scene_error": scene_error} if scene_error else {}))
        if not assigned:
            body["message"] = "sin asignar · elige una escena en el panel"
        # Spec §6.3/§7.1: a device holds its last good scene, and sends
        # If-None-Match here. /frame carried an ETag and this did not, so the
        # firmware's conditional GET was answered with a full body every time
        # and a holding device had to reconcile a payload it already had.
        #
        # Hashed on the body with its CLOCKS QUANTISED. The comment here used
        # to say this payload carried no clock; that stopped being true the
        # moment ages were recomputed at serve time, and the consequence was
        # measured: `dwell` advances continuously, so an empty sky 50 ms later
        # produced a different ETag and this route could never answer 304 at
        # all. The whole conditional-GET path was dead for the one scene that
        # uses it, and a device would parse a ~30 KB peak every poll to learn
        # nothing.
        #
        # Same trade as AGE_BUCKET_S on /data, for the same reason: a 304 may
        # hide at most one bucket of age drift, and the device advances the
        # ages itself from its own content clock in the meantime.
        etag = '"%s"' % hashlib.sha256(
            json.dumps(_scene_identity(body), sort_keys=True,
                       separators=(",", ":"),
                       ensure_ascii=False).encode()).hexdigest()[:16]
        if request.headers.get("If-None-Match") == etag:
            resp = Response(status=304)
        else:
            resp = jsonify(body)
        resp.headers["ETag"] = etag
        return _poll_header(resp, rec, scene, hw)

    def _requested_geometry(args: dict):
        """(w, h, error_response). Read from THIS request, never from storage.

        Two rounds of patching taught the lesson the hard way. A frame's length
        is the one thing a device cannot check -- it streams the body straight
        at the panel -- so any path where a THIRD party influences that length
        is a corrupt screen. Trusting stored caps was the first version;
        agreeing stored caps against the request was the second, and it fell to
        the same attack through the other route, because `/scene` still let an
        anonymous GET write the record that `/frame` then agreed with.

        The invariant that actually holds without authentication: the body is
        always exactly the length the CALLER asked for. A stranger claiming to
        be this device gets the frame they asked for and nobody else's panel
        changes. A device that states nothing gets a 400 rather than a guess,
        because a guess is where the wrong length came from every time.
        """
        asked = registry.clean_caps({k: v for k, v in args.items()
                                     if k in ("w", "h")})
        w, h = asked.get("w"), asked.get("h")
        if w is None or h is None:
            return 0, 0, (jsonify({
                "error": "declare the geometry you expect: ?w=<px>&h=<px>",
                "why": "a frame is raw pixels; its length is the device's "
                       "contract and the server will not guess it"}), 400)
        return int(w), int(h), None

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
        w, h, err = _requested_geometry(request.args.to_dict())
        if err:
            return err
        try:
            # Reject before building or rendering: a device declares its own
            # geometry, so this bounds what a device can make the Pi do.
            render_check_geometry(w, h)
        except RenderError as exc:
            return jsonify({"error": str(exc)}), 400
        name, scene = _scene_for(hw, rec, caps={"w": w, "h": h})
        _note(hw, **({"scene_error": scene.error} if scene.error else {}))
        if not scene.html:
            return jsonify({"error": f"scene {name!r} has no pixel rendering"}), 409
        if not render.is_cached(scene.html, w, h) \
                and not _cold_render_allowed(hw, rec,
                                             request.remote_addr or "?"):
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
        return _poll_header(resp, rec, scene, hw)

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
            # Four maps are written by unauthenticated requests and live for
            # the process lifetime. Each is capped, but a cap nothing can see
            # is a cap nothing checks: all four survived mutation because no
            # test could measure them. Reporting the sizes makes the bound
            # both testable and visible to an operator watching a Pi with
            # 1.8 GB of RAM.
            "memory": {"cold_render_ids": len(_last_cold),
                       "peer_buckets": len(_peer_bucket),
                       "serve_notes": len(_serve_notes),
                       "telemetry": len(telemetry),
                       "frame_cache": render.cache_stats()["size"]},
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
