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

import collections.abc
import contextlib
import fcntl
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Phase-1 stand-in. Phase 2 replaces this with real scene discovery; that is
# the seam. `unassigned` and `error` are server-chosen fallback states, so they
# are deliberately NOT assignable.
BUILTIN_SCENES = ("unassigned", "error")


def _assignable() -> tuple[str, ...]:
    """Derived from the scene table, not a second list that can drift."""
    from homescreen import scenes
    return scenes.names()


class _Assignable(collections.abc.Sequence):
    """Resolves lazily, so importing registry does not import every scene
    (scenes import cache, which would cycle).

    NOT a tuple subclass: as one, every un-overridden operation still saw the
    empty tuple it was constructed from -- `A == ()` was True, `A[0]` raised,
    and `BUILTIN_SCENES + A` silently dropped every scene. A Sequence has no
    inherited state to disagree with.
    """

    def __getitem__(self, index):
        return _assignable()[index]

    def __len__(self):
        return len(_assignable())

    def __contains__(self, item):
        return item in _assignable()

    def __eq__(self, other):
        return tuple(_assignable()) == tuple(other)

    def __hash__(self):
        return hash(_assignable())

    def __repr__(self):
        return repr(_assignable())


ASSIGNABLE_SCENES = _Assignable()

NAME_MAX = 64
HW_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
OFFLINE_AFTER_POLLS = 3

#: Record field: has an operator let this device into the fleet?
#:
#: Registration is unauthenticated by design -- anything on the LAN can POST a
#: hardware id and appear. That is fine for discovery and wrong for trust: the
#: fleet should be what someone CHOSE, not everything that ever spoke. So a new
#: record arrives `pending` and is inert until approved.
#:
#: Records written before this field existed are treated as approved. A gate
#: added later must not retroactively evict hardware that was already working;
#: the alternative is every panel in the house going blank on one deploy.
APPROVAL_FIELD = "approved"

#: What a device may be, from the fleet's point of view.
PENDING, APPROVED = "pending", "approved"
DEFAULT_POLL_SECONDS = 5
#: ADDENDUM: "An always-connected device can poll every 30s, receive 304 for a
#: few bytes most of the time, and partial-refresh on the minute." 5s is the
#: RADAR's cadence -- a 16-bit LCD that dead-reckons between polls and wants
#: fresh vectors. Applying it to a 1-bit panel asks for a refresh every 5s on
#: glass whose own full refresh takes ~3s, and holds ~58% of a render slot
#: (there are 2) forever, per panel. Depth is the honest discriminator: 1-bit
#: IS what makes it an e-paper.
EPAPER_POLL_SECONDS = 30

# Bounds on what one unauthenticated GET may persist. Without these, a single
# request writes tens of KB and an unbounded number of devices fills the microSD
# CLAUDE.md flags as unmitigated.
MAX_DEVICES = 64
MAX_TELEMETRY_KEYS = 16
MAX_VALUE_LEN = 128
MAX_CAP_LIST = 32
#: Options one assignment may carry. Mirrors scenes.MAX_OPTIONS.
MAX_OPTIONS = 12
CAP_INT_RANGE = (1, 4096)
#: Capability ints a device may declare. `max_items` is how many list entries
#: it can hold: the device knows its own RAM and the server does not.
CAP_INTS = ("w", "h", "depth", "max_items")

# A repeat poll only moves last_seen. Rewriting the file for that is the wear
# pattern write_failure already refuses; only persist once the stamp is stale
# enough to be worth recording.
MIN_LAST_SEEN_DELTA_S = 30.0

#: Contact times this process has observed, keyed by (registry path, hw id).
#: The wear guard above and the liveness window below were mutually
#: unsatisfiable as stated: `last_seen` reaches the disk at most every 30s,
#: while `is_online` calls a device offline after 3 x poll = 15s. Measured: a
#: device polling exactly on cadence read offline on 47% of checks -- and an
#: offline, unnamed device is EVICTABLE, so healthy hardware could be dropped
#: from a full registry. Memory is precise and free; the disk stays durable and
#: coarse. After a restart this is empty and we fall back to the disk, which is
#: honest: we genuinely have not heard from anyone yet.
_contact: dict[tuple, float] = {}
_contact_lock = threading.Lock()


def _note_contact(cache_dir: Path, hw_id: str, when: float, keep=None) -> None:
    """`keep` is the set of hw ids currently on disk.

    Evicting by AGE was self-defeating: every unauthenticated GET writes an
    entry, so a flood of rotating ids pushed out a real device's just-written
    contact and liveness fell back to the disk stamp -- which the 30s wear
    guard holds against a 15s window. Measured: a radar polling exactly on
    cadence read offline on 50% of checks WHILE the flood ran. The registry is
    already bounded at MAX_DEVICES, so bound this by the same set: an id that
    is not in the registry has nothing to be live about.
    """
    path = str(registry_path(cache_dir))
    with _contact_lock:
        _contact[(path, hw_id)] = when
        if len(_contact) > MAX_DEVICES * 2:
            live = set(keep or ())
            for key in list(_contact):
                if key[0] == path and key[1] not in live and key[1] != hw_id:
                    _contact.pop(key, None)


def _contact_time(cache_dir: Path, hw_id: str):
    with _contact_lock:
        return _contact.get((str(registry_path(cache_dir)), hw_id))


def forget_contacts() -> None:
    """Drop the in-process contact map. For tests and for a fresh start."""
    with _contact_lock:
        _contact.clear()


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


def approval(rec) -> str:
    """PENDING or APPROVED. Never raises; absent means approved (see above)."""
    if not isinstance(rec, dict):
        return PENDING
    value = rec.get(APPROVAL_FIELD)
    if value is None:
        return APPROVED                  # pre-dates the gate: grandfathered
    return APPROVED if value is True else PENDING


def is_approved(rec) -> bool:
    return approval(rec) == APPROVED


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
    if not isinstance(rec.get("options", {}), dict):
        return False
    if not isinstance(rec.get(APPROVAL_FIELD, True), bool):
        return False
    poll = rec.get("poll_seconds", DEFAULT_POLL_SECONDS)
    if poll is None:
        return True                      # nobody has chosen; derived per caps
    return isinstance(poll, (int, float)) and not isinstance(poll, bool)


def default_poll_seconds(caps) -> int:
    """The cadence a device gets when nobody has set one for it. Never raises.

    `caps` can be anything: devices.json is hand-editable and this runs on the
    serve path, where an AttributeError is a 500 on a device's only route.
    """
    if not isinstance(caps, dict):
        return DEFAULT_POLL_SECONDS
    try:
        depth = int(caps.get("depth"))
    except (TypeError, ValueError):
        return DEFAULT_POLL_SECONDS
    return EPAPER_POLL_SECONDS if depth == 1 else DEFAULT_POLL_SECONDS


#: Record field: the cadence CEILING last advertised to this device.
#:
#: Stored rather than recomputed because the fleet view has no scene: building
#: every device's scene to answer "is it online" would read feed caches and
#: render decisions on a route that only wants a timestamp comparison. What we
#: told the device is a fact about a past response, so it belongs in the record
#: next to `last_seen`, which is a fact about the same exchange.
POLL_BUDGET_FIELD = "poll_budget_s"


def poll_floor(caps) -> int:
    """The fastest cadence this HARDWARE can sustain, whatever a scene wants.

    A 1-bit panel's own full refresh takes ~3s and every frame it asks for is a
    Chromium render on the Pi, of which there are two slots for the whole
    fleet. A scene that wants a one-second tick -- a clock with seconds shown
    is a real example -- must not be able to turn that shared queue into a busy
    loop. A 16-bit LCD parses a few KB of JSON instead, so there is nothing
    there to protect and the scene gets what it asks for.

    Never raises: devices.json is hand-editable and this runs on the serve path.
    """
    if isinstance(caps, dict) and caps.get("depth") == 1:
        return EPAPER_POLL_SECONDS
    return 1


def poll_seconds(rec: dict, scene_poll_s=None) -> int:
    """What to put in `X-Poll-Seconds`. An operator's setting always wins.

    Rounds rather than truncates: a stored 1.9 became a header of 1, so the
    fleet view and the device disagreed and the device always polled faster
    than it was told to.

    Below an explicit setting sits the SCENE's own cadence, because what is
    being shown knows better than the panel's hardware does: a clock changes
    once a minute, a radar every few seconds, and the same device shows both.
    The hardware default stays underneath as the answer for a scene with no
    opinion -- it is about the glass (a 1-bit panel takes ~3s to refresh), not
    about the content.
    """
    raw = (rec or {}).get("poll_seconds")
    if raw is not None:
        try:
            n = int(round(float(raw)))
        except (TypeError, ValueError):
            n = 0
        if n >= 1:
            return n
    if scene_poll_s is not None and not isinstance(scene_poll_s, bool):
        # `float(True)` is 1.0, which would quietly become a one-second poll.
        try:
            n = int(round(float(scene_poll_s)))
        except (TypeError, ValueError):
            n = 0
        if n >= 1:
            return max(n, poll_floor((rec or {}).get("caps")))
    return default_poll_seconds((rec or {}).get("caps"))


def poll_budget_seconds(rec: dict, scene_max_s=None) -> int:
    """The window silence is judged against: the longest wait we would impose.

    Same precedence as `poll_seconds`, but fed the scene's CEILING rather than
    its next-wake time, so a clock counting down to the minute boundary is not
    judged dead for having been told "come back in 1s" a moment ago.
    """
    return poll_seconds(rec, scene_poll_s=scene_max_s)


def advertised_poll_seconds(rec) -> int:
    """The cadence to SHOW an operator: what this device was last told, or what
    it would be told if it asked right now.

    The dashboard and the liveness rule must quote one number. When they
    disagreed, the fleet called an e-paper offline against a cadence no route
    had ever sent it.
    """
    # Same precedence as everywhere else -- operator, then content, then
    # hardware -- with the recorded ceiling standing in for the scene. Reading
    # the record FIRST would let a cadence we advertised for a scene the device
    # no longer shows outrank the operator who just changed it.
    return poll_seconds(rec, scene_poll_s=(rec or {}).get(POLL_BUDGET_FIELD))


def remember_poll_budget(cache_dir: Path, hw_id: str, seconds: int) -> None:
    """Record what we just advertised. Never raises: this is bookkeeping on the
    device's only route, and failing to write it must not fail the response."""
    try:
        n = int(seconds)
    except (TypeError, ValueError):
        return
    if n < 1:
        return
    try:
        with _locked(cache_dir):
            data = load_raw(cache_dir)
            rec = data.get(hw_id)
            if not isinstance(rec, dict) or rec.get(POLL_BUDGET_FIELD) == n:
                return                   # unchanged: no write, no fsync
            rec[POLL_BUDGET_FIELD] = n
            _save_unlocked(cache_dir, data)
    except OSError:
        return


def load_raw(cache_dir: Path) -> dict:
    """Everything on disk, valid or not. Never raises.

    Mutators write back what THIS returns, not what `load` returns. Writing
    back the filtered view is read-repair-by-deletion: a single poll from any
    device would silently and permanently erase another device's name and
    scene. Measured before this split existed.
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
    return {k: v for k, v in data.items() if isinstance(k, str)}


def load(cache_dir: Path) -> dict:
    """Only the records safe to serve. Never raises.

    Invalid records are hidden from consumers -- they would 500 the serve path
    -- but are NOT removed from disk. Hiding is recoverable; deleting is not.
    """
    out = {}
    for key, rec in load_raw(cache_dir).items():
        if not _valid_record(rec):
            log.warning("registry record %r is malformed; hidden, not deleted", key)
            continue
        seen = _contact_time(cache_dir, key)
        disk = _epoch(rec.get("last_seen"))
        if seen is not None and (disk is None or seen > disk):
            # A view, never written back: `load_raw` stays the thing mutators
            # persist, so this cannot defeat the wear guard it exists beside.
            rec = {**rec, "last_seen": _stamp(seen)}
        out[key] = rec
    return out


def _quarantine(cache_dir: Path) -> None:
    """Move a corrupt registry aside rather than letting a write erase it.

    `load` degrading to {} plus a later `save` is data loss, not degradation:
    every real assignment disappears on the next restart.

    MUST be called inside `_locked`. It was not, and the parse-check and the
    rename were therefore not atomic against a concurrent writer: a poll would
    read a corrupt file, another writer would repair it, and the first would
    then file the now-VALID registry as corrupt -- taking a real panel's name
    and scene with it. Reproduced; the panel came back as "sin asignar".
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
    # os.link + unlink rather than rename: rename overwrites silently, and at
    # one-second resolution two quarantines in the same second targeted the
    # same name -- the second destroyed the first's evidence, which is the
    # whole point of quarantining.
    base = int(datetime.now(timezone.utc).timestamp())
    for suffix in range(100):
        dest = path.with_suffix(".corrupt-%d%s" % (base, f".{suffix}" if suffix else ""))
        try:
            os.link(path, dest)
        except FileExistsError:
            continue
        except OSError:
            # No hard links here (vfat, exfat, some NFS). Returning was worse
            # than the problem: the caller's next _save_unlocked overwrote the
            # corrupt file, destroying both the data and the evidence that is
            # the entire point of quarantining. Reserve the name exclusively,
            # then rename onto our own reservation.
            try:
                os.close(os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            except FileExistsError:
                continue
            except OSError as exc:
                log.error("registry corrupt and could not be moved aside: %s",
                          exc)
                return
            try:
                os.replace(path, dest)
            except OSError as exc:
                log.error("registry corrupt and could not be moved aside: %s",
                          exc)
                return
            log.error("registry was corrupt; moved to %s", dest.name)
            return
        path.unlink(missing_ok=True)
        log.error("registry was corrupt; moved to %s", dest.name)
        return
    log.error("registry corrupt and 100 quarantine names were taken; left in place")


#: Depth per (thread, cache_dir). flock is per-fd, so a second acquisition in
#: the same thread blocked on itself -- and that hang is unrecoverable: the
#: worker stops without the process dying, so systemd's Restart=always never
#: fires. Nothing nests today, but `_touch_locked`/`_assign_locked` exist
#: precisely so mutators can be composed, and the first composition would have
#: wedged the daemon.
_depth = threading.local()


@contextlib.contextmanager
def _locked(cache_dir: Path):
    """Hold an exclusive lock across a whole read-modify-write. Reentrant.

    Locking only the write is not enough and was a real bug here: a device poll
    and a human PATCH each load, each modify their own copy, and the second
    save silently discards the first's change. Measured -- the poll's firmware
    update vanished. Every mutating function below wraps load->modify->save in
    this, and calls _save_unlocked inside it.
    """
    key = str(registry_path(cache_dir))
    held = getattr(_depth, "held", None)
    if held is None:
        held = _depth.held = {}
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    path = registry_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lockfh:
        fcntl.flock(lockfh, fcntl.LOCK_EX)
        held[key] = 1
        try:
            yield
        finally:
            held[key] = 0


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
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    """A rename is only durable once the DIRECTORY entry is synced too."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def save(cache_dir: Path, data: dict) -> None:
    """Atomic write. Acquires the lock; use _save_unlocked inside _locked()."""
    with _locked(cache_dir):
        _save_unlocked(cache_dir, data)


def _check_hw(hw_id) -> str:
    if not isinstance(hw_id, str) or not HW_RE.match(hw_id.strip()):
        raise ValueError(f"unusable hardware id: {hw_id!r}")
    return hw_id.strip()


def bound_strings(raw, limit: int) -> dict:
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
    # CAP_INTS, not a second hand-written list. A literal here is how `max_items`
    # was accepted by the route, range-checked, and then silently dropped before
    # it reached the record -- the scene never saw it and the device would have
    # been sent a body it could not parse.
    for key in CAP_INTS:
        value = raw.get(key)
        if isinstance(value, bool):
            # int(True) is 1, which is a plausible-looking width. adsb_map._num
            # rejects bool for the same reason.
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
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
    with _locked(cache_dir):
        _quarantine(cache_dir)
        return _touch_locked(cache_dir, hw_id, fw, caps, telemetry, now)


def _touch_locked(cache_dir, hw_id, fw, caps, telemetry, now) -> dict:
    data = load_raw(cache_dir)          # raw: never write back a filtered view
    stamp = _stamp(now)
    rec = data.get(hw_id)
    fresh = rec is None

    if fresh:
        if len(data) >= MAX_DEVICES:
            # Registration is unauthenticated, so a device churning its id
            # would otherwise permanently lock out real hardware. Evict the
            # least useful record first: offline, never named, never assigned.
            # Liveness is NOT part of the test. Requiring the victim to be
            # offline meant 64 invented ids, kept fresh by one GET each, locked
            # out real hardware indefinitely -- the eviction rule protected
            # exactly the records with no value. What protects a record is an
            # operator having touched it: a name or a scene. Everything else is
            # a guess the server made, and the oldest guess goes first.
            evictable = sorted(
                (k for k, r in data.items()
                 if _valid_record(r) and not r.get("name")
                 and r.get("scene") in (None, "unassigned")),
                key=lambda k: data[k].get("last_seen") or "")
            if not evictable:
                raise ValueError(
                    f"registry is full ({MAX_DEVICES} devices) and every one is "
                    f"named, assigned or online; forget one before adding another")
            log.warning("registry full; evicting stale unassigned device %s",
                        evictable[0])
            del data[evictable[0]]
        rec = {"name": None, "scene": "unassigned", "first_seen": stamp,
               # A device that has never been let in. It still registers, so it
               # appears in the pending tray and an operator can see what is
               # asking -- but it is served nothing until someone says yes.
               APPROVAL_FIELD: False,
               # None, not DEFAULT_POLL_SECONDS. Storing the default at
               # registration masked `default_poll_seconds` entirely: every
               # device carried an explicit 5, so a 1-bit panel was still told
               # to come back in 5 seconds. Absent means "nobody has chosen",
               # which is the truth until an operator PATCHes one.
               "poll_seconds": None, "fw": None,
               # Per ASSIGNMENT, not per scene: two screens can show stocks
               # with different tickers. Validated against the scene's own
               # schema by whoever assigns, never trusted from disk.
               "options": {},
               "caps": {}, "telemetry": {}}
        log.info("new device registered: %s", hw_id)

    _note_contact(cache_dir, hw_id, _epoch(stamp) or 0.0, keep=data)
    # Telemetry is deliberately NOT in here. `uptime` and `errors` ride on
    # every poll, so including them made `changed` true every time and the
    # guard below never fired: measured at 5x write amplification, 17,280
    # full-file rewrites and fsyncs per device per day onto the microSD.
    # Telemetry still reaches the disk -- it just waits for a write we were
    # going to make anyway.
    before = {k: rec.get(k) for k in ("fw", "caps")}
    if fw is not None:
        rec["fw"] = str(fw)[:64]
    if caps:
        # Merge, don't replace: a device reporting one field later must not
        # erase the component list Phase 2 builds its scene from.
        rec["caps"] = {**rec.get("caps", {}), **clean_caps(caps)}
    if telemetry:
        rec["telemetry"] = bound_strings(telemetry, MAX_TELEMETRY_KEYS)

    changed = fresh or any(rec.get(k) != v for k, v in before.items())
    prev = _epoch(rec.get("last_seen"))
    # abs(): a stamp AHEAD of us is never refreshed by a positive test, so a
    # Pi that boots at a saved time ahead of true time (no RTC) shows every
    # quiet device offline for the whole skew and never heals. cache.py:_write
    # restamps unconditionally for the same reason; the registry now matches.
    stale = prev is None or abs((_epoch(stamp) or 0) - prev) >= MIN_LAST_SEEN_DELTA_S
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
           poll_seconds=None, options=None) -> dict:
    """Set name/scene/poll_seconds/options. Validates BEFORE persisting.

    `options` must already be coerced by the caller against the scene's schema
    (`scenes.clean_options`) -- this module does not import scenes, and should
    not: the registry stores what a device is, not what a component means.
    """
    with _locked(cache_dir):
        _quarantine(cache_dir)
        return _assign_locked(cache_dir, hw_id, name, scene, poll_seconds,
                              options)


def _assign_locked(cache_dir, hw_id, name, scene, poll_seconds,
                   options=None) -> dict:
    data = load_raw(cache_dir)
    if hw_id not in data:
        raise ValueError(f"unknown device: {hw_id}")
    if not _valid_record(data[hw_id]):
        raise ValueError(f"device {hw_id} has a malformed record; "
                         f"delete it or repair devices.json")
    rec = dict(data[hw_id])

    if name is not None:
        rec["name"] = _check_name(data, hw_id, name)
    if scene is not None:
        # "unassigned" is how a device says it has no scene, and an operator
        # must be able to put it back there -- to take a screen out of service,
        # or to read its hardware id off the glass again. Rejecting it meant the
        # only way back was DELETE, which throws away the name and the history
        # with it. "error" stays unsettable: it is a state the server enters,
        # not one an operator chooses.
        if scene != "unassigned" and scene not in ASSIGNABLE_SCENES:
            raise ValueError(f"unknown scene {scene!r}; assignable: "
                             f"{', '.join(ASSIGNABLE_SCENES)}, unassigned")
        if scene != rec.get("scene") and options is None:
            # A different component takes different options. Carrying the old
            # ones forward would leave a clock configured with a stock ticker's
            # settings -- present, meaningless, and invisible in the form.
            rec["options"] = {}
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

    if options is not None:
        if not isinstance(options, dict):
            raise ValueError(f"options must be a mapping, got {options!r}")
        if len(options) > MAX_OPTIONS:
            raise ValueError(f"at most {MAX_OPTIONS} options, got {len(options)}")
        # Bounded on the way in, like every other network-written field here.
        rec["options"] = {
            str(k)[:32]: (v if isinstance(v, (bool, int, float))
                          else str(v)[:MAX_VALUE_LEN])
            for k, v in list(options.items())[:MAX_OPTIONS]}

    if rec == data[hw_id]:
        # Nothing changed, so nothing is written. The dashboard has an apply
        # button and people press it twice; the poll path has had this guard
        # since the beginning and this one did not. Validation above still ran,
        # so a bad value is still refused -- this only skips the fsync.
        return rec

    data[hw_id] = rec
    _save_unlocked(cache_dir, data)
    return rec


def set_approval(cache_dir: Path, hw_id: str, approved: bool) -> dict | None:
    """Let a device into the fleet, or put it back outside. None if unknown.

    Revoking does not delete: the record keeps its name, assignment and history
    so letting it back in is one click rather than a re-setup. Deleting is
    `forget`, and is a different decision.
    """
    hw_id = _check_hw(hw_id)
    with _locked(cache_dir):
        data = load_raw(cache_dir)
        rec = data.get(hw_id)
        if not isinstance(rec, dict):
            return None
        if rec.get(APPROVAL_FIELD) is bool(approved):
            return rec                   # unchanged: no write, no fsync
        rec[APPROVAL_FIELD] = bool(approved)
        data[hw_id] = rec
        _save_unlocked(cache_dir, data)
        return rec


def pending(cache_dir: Path) -> dict:
    """Records waiting to be let in, oldest request first."""
    return {k: v for k, v in sorted(load(cache_dir).items(),
                                    key=lambda kv: kv[1].get("first_seen") or "")
            if not is_approved(v)}


def forget(cache_dir: Path, hw_id: str) -> bool:
    with _locked(cache_dir):
        data = load_raw(cache_dir)
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
    # The cadence we ADVERTISED, not the bare default: an e-paper is told 30s,
    # so judging it against 3 x 5s would call a device offline two polls before
    # it was ever due to speak. These two numbers must come from one place.
    # The CEILING we advertised, not the countdown value: a clock aiming at the
    # minute boundary is told 1s at :59, and three times that would call it
    # dead one second later. `poll_budget_s` is what the scene route recorded.
    poll = advertised_poll_seconds(rec)
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
    # The one load-modify-save that sat outside the lock. It runs before the
    # listener binds, so nothing raced it in practice -- but it is a violation
    # of this module's own stated discipline, and it stops being theoretical
    # the moment a second writer exists. Reproduced with an injected delay: a
    # concurrent poll's brand-new device vanished.
    with _locked(cache_dir):
        _quarantine(cache_dir)
        return _seed_locked(cache_dir, devices, marker, now)


def _seed_locked(cache_dir: Path, devices: list, marker: Path, now) -> int:
    data = load_raw(cache_dir)

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
        try:
            poll = float(dev.get("poll_seconds") or DEFAULT_POLL_SECONDS)
            if not 1.0 <= poll <= 3600.0:
                raise ValueError
        except (TypeError, ValueError):
            log.warning("seeding %s: poll_seconds %r unusable, using default",
                        hw_id, dev.get("poll_seconds"))
            poll = DEFAULT_POLL_SECONDS
        if scene is not None and scene not in ASSIGNABLE_SCENES:
            log.warning("seeding %s: config names unknown scene %r; unassigned",
                        hw_id, scene)
        record = {
            "name": name,
            "scene": scene if scene in ASSIGNABLE_SCENES else "unassigned",
            "first_seen": stamp, "last_seen": stamp,
            "poll_seconds": poll,
            "fw": "config", "caps": {}, "telemetry": {},
        }
        if not _valid_record(record):
            # Writing a record our own validator rejects means it vanishes on
            # the next read AND the marker stops us retrying. Refuse instead.
            log.error("seeding %s produced an invalid record; skipped", hw_id)
            continue
        data[hw_id] = record
        seeded += 1

    _save_unlocked(cache_dir, data)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(stamp)
    log.info("seeded %d device(s) from config.yaml", seeded)
    return seeded
