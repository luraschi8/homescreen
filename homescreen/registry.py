"""Device registry: who is out there, what they are, what they show.

Devices self-register by immutable hardware id on first contact. The friendly
name is server-side presentation only -- a device cannot poll by a name it does
not yet have, and a name in the URL would make renaming a device a reflash and
a name collision a silent misroute.

WRITE PROFILE -- read this before changing anything here.

This module looks like `overrides.py` and must not behave like it. `overrides`
is written only by explicit human action, which is what makes its
degrade-on-corrupt, its lack of locking and its whole-file rewrite all safe.
The registry is written on every device poll (~17k/device/day), at startup, and
from unauthenticated network input. Every guard below exists because one of
those three differences turns an `overrides` pattern into a bug:

  poll writes      -> skip no-op writes, or it is 17k SD writes/device/day
  network writes   -> bound key count, value length and device count
  startup writes   -> a corrupt file must be moved aside, never overwritten,
                      and a write failure must never reach startup's handler
  concurrent       -> lock the read-modify-write; app.run() is threaded
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Phase-1 stand-in. Phase 2 replaces this with real scene discovery; that is
# the seam. `unassigned` and `error` are server-chosen fallback states, so they
# are deliberately NOT assignable.
BUILTIN_SCENES = ("unassigned", "error")
ASSIGNABLE_SCENES = ("planes",)
KNOWN_SCENES = BUILTIN_SCENES + ASSIGNABLE_SCENES

NAME_MAX = 64
HW_MAX = 128
HW_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
OFFLINE_AFTER_POLLS = 3
DEFAULT_POLL_SECONDS = 5

# Bounds on what one unauthenticated GET may persist. Without these, a single
# request writes tens of KB and an unbounded number of devices fills the microSD
# CLAUDE.md flags as unmitigated.
MAX_DEVICES = 64
MAX_TELEMETRY_KEYS = 16
MAX_VALUE_LEN = 128
MAX_CAP_LIST = 32
CAP_INT_RANGE = (1, 4096)

# A repeat poll only moves last_seen. Rewriting the file for that is the wear
# pattern write_failure already refuses; only persist once the stamp is stale
# enough to be worth recording.
MIN_LAST_SEEN_DELTA_S = 30.0


def registry_path(cache_dir: Path) -> Path:
    return cache_dir / "devices.json"


def _stamp(now: float | None) -> str:
    """One time source. `touch` taking `now` and ignoring it made liveness
    untestable and several tests pass against a 56-year-negative delta."""
    when = datetime.now(timezone.utc) if now is None \
        else datetime.fromtimestamp(now, timezone.utc)
    return when.astimezone().isoformat()


def _epoch(stamp) -> float | None:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def _valid_record(rec) -> bool:
    """A parseable file is not a valid one. Both consumers index these fields
    unguarded, and a wrong-typed value 500s the serve path."""
    if not isinstance(rec, dict):
        return False
    for key in ("name", "scene", "fw"):
        if not isinstance(rec.get(key), (str, type(None))):
            return False
    for key in ("first_seen", "last_seen"):
        if not isinstance(rec.get(key), str):
            return False
    if not isinstance(rec.get("caps", {}), dict):
        return False
    if not isinstance(rec.get("telemetry", {}), dict):
        return False
    poll = rec.get("poll_seconds", DEFAULT_POLL_SECONDS)
    return isinstance(poll, (int, float)) and not isinstance(poll, bool)


def load(cache_dir: Path) -> dict:
    """{hw_id: record}. Never raises. Drops records that fail validation.

    Does NOT rewrite the file -- see `_quarantine`. A read that repairs is a
    read that can destroy.
    """
    try:
        with open(registry_path(cache_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.warning("registry unreadable (%s); treating as empty", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and _valid_record(v)}


def _quarantine(cache_dir: Path) -> None:
    """Move a corrupt registry aside rather than letting a write erase it.

    `load` degrading to {} plus a later `save` is data loss, not degradation:
    every real assignment disappears on the next restart.
    """
    path = registry_path(cache_dir)
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as fh:
            json.load(fh)
        return                       # parses fine; nothing to quarantine
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    dest = path.with_suffix(f".corrupt-{int(datetime.now().timestamp())}")
    try:
        path.rename(dest)
        log.error("registry was corrupt; moved to %s", dest.name)
    except OSError as exc:
        log.error("registry corrupt and could not be moved aside: %s", exc)


@contextlib.contextmanager
def _locked(cache_dir: Path):
    """Hold an exclusive lock across a whole read-modify-write.

    Locking only the write is not enough and was a real bug here: a device poll
    and a human PATCH each load, each modify their own copy, and the second
    save silently discards the first's change. Measured -- the poll's firmware
    update vanished. Every mutating function below wraps load->modify->save in
    this, and calls _save_unlocked inside it.
    """
    path = registry_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lockfh:
        fcntl.flock(lockfh, fcntl.LOCK_EX)
        yield


def _save_unlocked(cache_dir: Path, data: dict) -> None:
    path = registry_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, allow_nan=False)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)


def save(cache_dir: Path, data: dict) -> None:
    """Atomic write. Acquires the lock; use _save_unlocked inside _locked()."""
    with _locked(cache_dir):
        _save_unlocked(cache_dir, data)


def _check_hw(hw_id) -> str:
    if not isinstance(hw_id, str) or not HW_RE.match(hw_id.strip()):
        raise ValueError(f"unusable hardware id: {hw_id!r}")
    return hw_id.strip()


def _bound_strings(raw, limit: int) -> dict:
    """Cap what the network can persist: key count, and value length."""
    out = {}
    for key, value in list(raw.items())[:limit]:
        if isinstance(key, str) and len(key) <= 32:
            out[key[:32]] = str(value)[:MAX_VALUE_LEN]
    return out


def clean_caps(raw) -> dict:
    """Whitelist and range-check declared capabilities.

    `depth=-5` and `w=99999999999999999999` are not capabilities; Phase 2 sizes
    a scene from these, so nonsense here becomes nonsense on a screen.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    lo, hi = CAP_INT_RANGE
    for key in ("w", "h", "depth"):
        try:
            n = int(raw[key])
        except (KeyError, TypeError, ValueError):
            continue
        if lo <= n <= hi:
            out[key] = n
    for key in ("layouts", "components"):
        value = raw.get(key)
        if isinstance(value, list):
            out[key] = [str(v)[:32] for v in value[:MAX_CAP_LIST]
                        if isinstance(v, str) and v.strip()]
    return out


def touch(cache_dir: Path, hw_id, *, fw=None, caps=None, telemetry=None,
          now: float | None = None) -> dict:
    """Register on first contact, update on later ones. Never raises on I/O.

    Never clears an existing name or scene: a reflash reports a new firmware
    version from the same board and must not orphan its assignment.
    """
    hw_id = _check_hw(hw_id)
    _quarantine(cache_dir)
    with _locked(cache_dir):
        return _touch_locked(cache_dir, hw_id, fw, caps, telemetry, now)


def _touch_locked(cache_dir, hw_id, fw, caps, telemetry, now) -> dict:
    data = load(cache_dir)
    stamp = _stamp(now)
    rec = data.get(hw_id)
    fresh = rec is None

    if fresh:
        if len(data) >= MAX_DEVICES:
            raise ValueError(f"registry is full ({MAX_DEVICES} devices); "
                             f"forget one before adding another")
        rec = {"name": None, "scene": "unassigned", "first_seen": stamp,
               "poll_seconds": DEFAULT_POLL_SECONDS, "fw": None,
               "caps": {}, "telemetry": {}}
        log.info("new device registered: %s", hw_id)

    before = {k: rec.get(k) for k in ("fw", "caps", "telemetry")}
    if fw is not None:
        rec["fw"] = str(fw)[:64]
    if caps:
        # Merge, don't replace: a device reporting one field later must not
        # erase the component list Phase 2 builds its scene from.
        rec["caps"] = {**rec.get("caps", {}), **clean_caps(caps)}
    if telemetry:
        rec["telemetry"] = _bound_strings(telemetry, MAX_TELEMETRY_KEYS)

    changed = fresh or any(rec.get(k) != v for k, v in before.items())
    prev = _epoch(rec.get("last_seen"))
    stale = prev is None or (_epoch(stamp) or 0) - prev >= MIN_LAST_SEEN_DELTA_S
    rec["last_seen"] = stamp

    if changed or stale:
        data[hw_id] = rec
        _save_unlocked(cache_dir, data)
    return rec


def _check_name(data: dict, hw_id: str, name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    name = name.strip()
    if "/" in name or len(name) > NAME_MAX:
        raise ValueError(f"unusable name: {name!r}")
    for other, rec in data.items():
        if other != hw_id and rec.get("name") == name:
            raise ValueError(f"name {name!r} is already used by {other}")
    return name


def assign(cache_dir: Path, hw_id: str, *, name=None, scene=None,
           poll_seconds=None) -> dict:
    """Set name/scene/poll_seconds. Validates everything BEFORE persisting."""
    _quarantine(cache_dir)
    with _locked(cache_dir):
        return _assign_locked(cache_dir, hw_id, name, scene, poll_seconds)


def _assign_locked(cache_dir, hw_id, name, scene, poll_seconds) -> dict:
    data = load(cache_dir)
    if hw_id not in data:
        raise ValueError(f"unknown device: {hw_id}")
    rec = dict(data[hw_id])

    if name is not None:
        rec["name"] = _check_name(data, hw_id, name)
    if scene is not None:
        if scene not in ASSIGNABLE_SCENES:
            raise ValueError(f"unknown scene {scene!r}; assignable: "
                             f"{', '.join(ASSIGNABLE_SCENES)}")
        rec["scene"] = scene
    if poll_seconds is not None:
        try:
            n = float(poll_seconds)
        except (TypeError, ValueError):
            raise ValueError(f"poll_seconds must be a number, "
                             f"got {poll_seconds!r}") from None
        if not 1.0 <= n <= 3600.0:
            raise ValueError(f"poll_seconds must be 1 to 3600, got {n}")
        rec["poll_seconds"] = n

    data[hw_id] = rec
    _save_unlocked(cache_dir, data)
    return rec


def forget(cache_dir: Path, hw_id: str) -> bool:
    with _locked(cache_dir):
        data = load(cache_dir)
        if hw_id not in data:
            return False
        del data[hw_id]
        _save_unlocked(cache_dir, data)
        return True


def resolve_name(cache_dir: Path, name: str) -> str | None:
    for hw_id, rec in sorted(load(cache_dir).items()):
        if rec.get("name") == name:
            return hw_id
    return None


def is_online(rec: dict, now: float) -> bool:
    """Derived, never stored. Never raises.

    The future-stamp guard mirrors serve._feed_state: a Pi 4 has no RTC, boots
    at the time timesyncd last saved, and jumps when NTP lands -- so a stamp
    written pre-sync sits ahead of us. Without this, such a device reads online
    forever, which is the opposite of what a fleet view is for.
    """
    if not isinstance(rec, dict):
        return False
    seen = _epoch(rec.get("last_seen"))
    if seen is None:
        return False
    try:
        poll = float(rec.get("poll_seconds") or DEFAULT_POLL_SECONDS)
    except (TypeError, ValueError):
        poll = DEFAULT_POLL_SECONDS
    delta = now - seen
    if delta < -1.0:
        return False                    # clock skew, not freshness
    return delta < OFFLINE_AFTER_POLLS * poll


def seeded_marker_path(cache_dir: Path) -> Path:
    """A sidecar file, not a record inside devices.json.

    A marker stored as a record passes `_valid_record` and would render as a
    phantom device in the fleet view -- caught before this ever ran.
    """
    return cache_dir / "devices.seeded"


def seed_from_config(cfg: dict, cache_dir: Path, *, now: float | None = None) -> int:
    """Seed registry records from devices still declared in config.yaml.

    Runs ONCE, marked -- spec §4.7 says the config device section is "read once
    and never again". Re-running would resurrect a record you deleted while
    adopting the real board, leaving two devices with the same friendly name,
    written by a path that bypasses `_check_name` entirely.

    The id is synthetic (`cfg:<id>`) because config.yaml names a device but
    cannot know which physical board it meant.
    """
    devices = cfg.get("devices")
    if not isinstance(devices, list):
        return 0
    marker = seeded_marker_path(cache_dir)
    if marker.exists():
        return 0
    _quarantine(cache_dir)
    data = load(cache_dir)

    stamp = _stamp(now)
    seeded = 0
    for dev in devices:
        if not isinstance(dev, dict) or not isinstance(dev.get("id"), str):
            continue
        hw_id = f"cfg:{dev['id']}"
        if hw_id in data:
            continue
        try:
            name = _check_name(data, hw_id, dev["id"])
        except ValueError as exc:
            log.warning("seeding %s unnamed: %s", hw_id, exc)
            name = None
        scene = dev.get("scene")
        data[hw_id] = {
            "name": name,
            "scene": scene if scene in ASSIGNABLE_SCENES else "unassigned",
            "first_seen": stamp, "last_seen": stamp,
            "poll_seconds": dev.get("poll_seconds") or DEFAULT_POLL_SECONDS,
            "fw": "config", "caps": {}, "telemetry": {},
        }
        seeded += 1

    save(cache_dir, data)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(stamp)
    log.info("seeded %d device(s) from config.yaml", seeded)
    return seeded
