# Device Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Devices self-register with the Pi, appear in a fleet view with their firmware version and online state, and are assigned a scene from the server — all against the radar firmware exactly as it is today.

**Architecture:** A registry file the server writes (`cache/devices.json`), mirroring the existing `overrides.py` data-layer pattern. Devices are keyed by immutable hardware id and always talk by that id; a friendly name is server-side presentation only, which removes the bootstrap problem of a device needing to know its name before it has one. Liveness is derived from `last_seen`, never stored. `serve.py` is at 492 lines carrying five concerns, so its HTML rendering moves to `web.py` before the fleet dashboard grows it further.

**Tech Stack:** Python 3.13 (Pi) / 3.14 (Mac), Flask, pytest, pytest-timeout. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-26-server-driven-displays-design.md` — Phase 1 is §4 in full. §5 (scenes) and §7 (firmware) are out of scope here.

## Machine convention

**Every step is labelled `Runs on: Mac` or `Runs on: Pi`.** Tasks 1–6 are Mac-only. Task 7 deploys. Deploy path on the Pi is `/home/pi/dashboard`.

**Every Mac code block begins with `cd /Users/matias/Documents/repos/HomeScreen`** — tasks may be executed by separate subagents whose shell cwd resets between calls.

## Global Constraints

- **Nothing may raise into the serve path. Ever.** A corrupt `devices.json` degrades to an empty registry, exactly as `overrides.json` already does. (SPEC §11.1, spec §6.4)
- **The registry is written from the network.** Nothing reaches disk unvalidated — the same rule the config API already follows.
- **`/api/display/radar/data` keeps working throughout.** Phase 1 ships against unmodified firmware; a regression here breaks a live deployment. (spec §4.6)
- **Liveness is derived, never stored** — `last_seen` older than 3× the device's poll interval. (spec §4.4)
- **The device always talks by hardware id.** Friendly names appear only in the fleet view and the human alias. (spec §4.2)
- No authentication, no pending-approval state. LAN service. (spec §4.8)
- Timestamps are ISO8601 **with a UTC offset**, via `cache._now_iso`. A naive stamp is silently reinterpreted as local time on parse.

---

## File Structure

| File | Responsibility |
|---|---|
| `homescreen/registry.py` | **New.** Registry data layer: load, save, touch, assign, forget, liveness, migration. Mirrors `overrides.py`. No Flask. |
| `homescreen/web.py` | **New.** All HTML rendering, moved out of `serve.py`. Grows with the fleet dashboard. |
| `homescreen/serve.py` | Routes and wiring only. Loses ~110 lines to `web.py`, gains ~70 for registry routes. |
| `homescreen/config.py` | Gains `scene` as a device key for migration. |
| `tests/test_registry.py` | **New.** The data layer, no Flask. |
| `tests/test_devices_api.py` | **New.** The endpoints. |
| `tests/test_home.py` | Extended for the fleet dashboard. |

`registry.py` deliberately has no Flask import: it is the only module the fetch daemon could ever need to read device state from, and the C7 guard forbids the serve path pulling in network modules — keeping the data layer framework-free avoids the mirror-image problem.

---

## Task 1: Registry data layer

**Runs on: Mac**

**Files:**
- Create: `homescreen/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `cache._now_iso`, `cache._write`-style atomic write (reimplemented locally, as `overrides.py` does)
- Produces:
  - `registry_path(cache_dir: Path) -> Path`
  - `load(cache_dir: Path) -> dict` — `{hw_id: record}`; never raises
  - `save(cache_dir: Path, data: dict) -> None` — atomic
  - `touch(cache_dir, hw_id, *, fw=None, caps=None, telemetry=None, now=None) -> dict` — register on first contact, update on later ones; returns the record
  - `assign(cache_dir, hw_id, *, name=None, scene=None, poll_seconds=None) -> dict` — raises `ValueError`
  - `forget(cache_dir, hw_id) -> bool`
  - `resolve_name(cache_dir, name) -> str | None` — friendly name → hw id
  - `is_online(record: dict, now: float) -> bool`
  - `KNOWN_SCENES: tuple[str, ...]`

> **`KNOWN_SCENES` is a phase-1 stand-in.** Scenes do not exist until Phase 2, but assignment must still be validated — writing an unknown scene name and discovering it at render time is exactly the "validated before persisting" rule this project already follows elsewhere. Phase 2 replaces this constant with real scene discovery; that is the seam.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry.py
import json
from pathlib import Path

import pytest

from homescreen import registry

HW = "a4cf12ab3c44"
CAPS = {"w": 240, "h": 240, "depth": 16, "layouts": ["fill"],
        "components": ["text", "rings", "markers"]}


def test_first_contact_registers_an_unassigned_device(tmp_path: Path):
    rec = registry.touch(tmp_path, HW, fw="0.2.0", caps=CAPS, now=1000.0)
    assert rec["name"] is None, "unnamed until a human assigns one"
    assert rec["scene"] == "unassigned"
    assert rec["fw"] == "0.2.0"
    assert rec["caps"] == CAPS
    assert rec["first_seen"] == rec["last_seen"]
    assert registry.load(tmp_path)[HW]["fw"] == "0.2.0", "persisted"


def test_later_contact_updates_without_losing_assignment(tmp_path: Path):
    registry.touch(tmp_path, HW, fw="0.2.0", caps=CAPS, now=1000.0)
    registry.assign(tmp_path, HW, name="radar", scene="planes")
    rec = registry.touch(tmp_path, HW, fw="0.3.0",
                         telemetry={"rssi": -64}, now=2000.0)
    assert rec["name"] == "radar", "a reflash must not orphan the device"
    assert rec["scene"] == "planes"
    assert rec["fw"] == "0.3.0", "but the version does update"
    assert rec["telemetry"] == {"rssi": -64}
    assert rec["first_seen"] != rec["last_seen"]


def test_liveness_is_derived_from_last_seen(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0)
    registry.assign(tmp_path, HW, poll_seconds=5)
    rec = registry.load(tmp_path)[HW]
    assert registry.is_online(rec, 1010.0) is True, "2 polls late is fine"
    assert registry.is_online(rec, 1020.0) is False, "3x the interval is offline"
    assert "online" not in rec, "derived, never stored"


def test_liveness_never_raises_on_a_damaged_record():
    for rec in ({}, {"last_seen": None}, {"last_seen": "garbage"},
                {"last_seen": "2026-01-01T00:00:00+00:00", "poll_seconds": "x"}):
        assert registry.is_online(rec, 1000.0) is False


@pytest.mark.parametrize("kwargs,why", [
    ({"name": ""}, "empty name"),
    ({"name": "   "}, "whitespace name"),
    ({"name": "has/slash"}, "would break the URL alias"),
    ({"name": "a" * 65}, "absurd length"),
    ({"scene": "no-such-scene"}, "unknown scene"),
    ({"poll_seconds": 0}, "would make liveness meaningless"),
    ({"poll_seconds": "soon"}, "not a number"),
])
def test_assign_rejects_bad_values_without_persisting(tmp_path, kwargs, why):
    registry.touch(tmp_path, HW, now=1000.0)
    before = registry.load(tmp_path)[HW]
    with pytest.raises(ValueError):
        registry.assign(tmp_path, HW, **kwargs)
    assert registry.load(tmp_path)[HW] == before, f"{why}: nothing may reach disk"


def test_names_must_be_unique(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0)
    registry.touch(tmp_path, "ffff0000ffff", now=1000.0)
    registry.assign(tmp_path, HW, name="radar")
    with pytest.raises(ValueError, match="already"):
        registry.assign(tmp_path, "ffff0000ffff", name="radar")
    # Re-assigning the SAME name to the SAME device is not a collision.
    registry.assign(tmp_path, HW, name="radar")


def test_resolve_name_maps_friendly_name_to_hardware_id(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0)
    registry.assign(tmp_path, HW, name="radar")
    assert registry.resolve_name(tmp_path, "radar") == HW
    assert registry.resolve_name(tmp_path, "nope") is None


def test_forget_removes_a_retired_board(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0)
    assert registry.forget(tmp_path, HW) is True
    assert registry.load(tmp_path) == {}
    assert registry.forget(tmp_path, HW) is False


@pytest.mark.parametrize("junk", [
    "{not json", "[]", '"a string"', "null",
    '{"hw": "not a record"}', '{"hw": [1,2]}',
])
def test_a_corrupt_registry_degrades_to_empty(tmp_path: Path, junk):
    # Written from the network; a bad one must never wedge the daemon.
    registry.registry_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    registry.registry_path(tmp_path).write_text(junk)
    assert registry.load(tmp_path) == {}


def test_touch_rejects_an_unusable_hardware_id(tmp_path: Path):
    for bad in ("", "   ", "has/slash", "x" * 129, None, 5):
        with pytest.raises(ValueError):
            registry.touch(tmp_path, bad, now=1000.0)
    assert registry.load(tmp_path) == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_registry.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'homescreen.registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# homescreen/registry.py
"""Device registry: who is out there, what they are, what they show.

Devices self-register by immutable hardware id on first contact. The friendly
name is server-side presentation only -- a device cannot poll by a name it does
not yet have, so naming it in the URL would create a bootstrap problem with no
good answer.

This module deliberately imports no Flask: it is the data layer, and keeping it
framework-free means the fetch daemon could read device state without pulling a
web framework into a process that has no business with one.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from homescreen.cache import _now_iso

log = logging.getLogger(__name__)

# Phase-1 stand-in. Scenes arrive in Phase 2, which replaces this with real
# discovery -- but assignment must be validated NOW, because writing an unknown
# scene and discovering it at render time is the failure this project already
# refuses everywhere else.
KNOWN_SCENES = ("unassigned", "error", "planes")

NAME_MAX = 64
HW_MAX = 128
OFFLINE_AFTER_POLLS = 3
DEFAULT_POLL_SECONDS = 5


def registry_path(cache_dir: Path) -> Path:
    return cache_dir / "devices.json"


def load(cache_dir: Path) -> dict:
    """{hw_id: record}. Never raises; a corrupt file reads as empty."""
    try:
        with open(registry_path(cache_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, dict)}


def save(cache_dir: Path, data: dict) -> None:
    """Atomic, so a reader never sees a half-written registry."""
    path = registry_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, allow_nan=False)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)


def _check_hw(hw_id) -> str:
    if not isinstance(hw_id, str) or not hw_id.strip():
        raise ValueError(f"hardware id must be a non-empty string, got {hw_id!r}")
    if "/" in hw_id or len(hw_id) > HW_MAX:
        raise ValueError(f"unusable hardware id: {hw_id!r}")
    return hw_id


def touch(cache_dir: Path, hw_id, *, fw=None, caps=None, telemetry=None,
          now: float | None = None) -> dict:
    """Register on first contact, update on every later one.

    Never clears an existing name or scene: a reflash reports a new firmware
    version from the same board, and must not orphan its assignment.
    """
    _check_hw(hw_id)
    data = load(cache_dir)
    stamp = _now_iso()
    rec = data.get(hw_id)
    if rec is None:
        rec = {"name": None, "scene": "unassigned",
               "first_seen": stamp, "poll_seconds": DEFAULT_POLL_SECONDS,
               "fw": None, "caps": {}, "telemetry": {}}
        log.info("new device registered: %s", hw_id)
    rec["last_seen"] = stamp
    if fw is not None:
        rec["fw"] = str(fw)[:64]
    if isinstance(caps, dict):
        rec["caps"] = caps
    if isinstance(telemetry, dict):
        rec["telemetry"] = telemetry
    data[hw_id] = rec
    save(cache_dir, data)
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
    data = load(cache_dir)
    if hw_id not in data:
        raise ValueError(f"unknown device: {hw_id}")
    rec = dict(data[hw_id])

    if name is not None:
        rec["name"] = _check_name(data, hw_id, name)
    if scene is not None:
        if scene not in KNOWN_SCENES:
            raise ValueError(f"unknown scene {scene!r}; known: "
                             f"{', '.join(KNOWN_SCENES)}")
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
    save(cache_dir, data)
    return rec


def forget(cache_dir: Path, hw_id: str) -> bool:
    data = load(cache_dir)
    if hw_id not in data:
        return False
    del data[hw_id]
    save(cache_dir, data)
    return True


def resolve_name(cache_dir: Path, name: str) -> str | None:
    for hw_id, rec in load(cache_dir).items():
        if rec.get("name") == name:
            return hw_id
    return None


def is_online(rec: dict, now: float) -> bool:
    """Derived, never stored -- a stored flag needs a sweep and can go stale.

    Never raises: this is called once per device per page render, and a damaged
    record must show as offline rather than break the fleet view.
    """
    try:
        seen = datetime.fromisoformat(rec["last_seen"]).timestamp()
        poll = float(rec.get("poll_seconds") or DEFAULT_POLL_SECONDS)
    except (KeyError, TypeError, ValueError):
        return False
    return (now - seen) < OFFLINE_AFTER_POLLS * poll
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_registry.py -q
```

Expected: PASS — **19 tests**

- [ ] **Step 5: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/registry.py tests/test_registry.py
git commit -m "Add device registry data layer with derived liveness"
```

---

## Task 2: Extract HTML rendering into web.py

**Runs on: Mac**

**Files:**
- Create: `homescreen/web.py`
- Modify: `homescreen/serve.py` — remove `_HOME_CSS`, `_render_home`, `_duration`, and the `html` import

**Interfaces:**
- Consumes: nothing new
- Produces: `web.render_home(status: dict) -> str`, `web.duration(seconds: float) -> str`

> **Pure refactor: no test changes, no behaviour change.** `serve.py` is 492 lines carrying five concerns; the fleet dashboard would push it past 600. Doing the move now, while the suite is green and the page output is byte-stable, means Task 6 is a feature change rather than a feature-plus-refactor.

- [ ] **Step 1: Capture the current output as a baseline**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/python - <<'EOF'
from pathlib import Path
from homescreen.serve import create_app
cfg = {"feeds": {"adsb": {"source": "api", "endpoint": "https://x", "fetch_seconds": 3}},
       "devices": [{"id": "radar", "kind": "gc9a01_client", "render": "device",
                    "feed": "adsb", "poll_seconds": 5}]}
c = create_app(cfg, Path("/tmp/baseline-cache"), version="baseline").test_client()
Path("/tmp/home-before.html").write_text(c.get("/home").get_data(as_text=True))
print("baseline captured:", len(Path("/tmp/home-before.html").read_text()), "bytes")
EOF
```

- [ ] **Step 2: Move the three symbols mechanically**

This is a pure move — no retyping, so there is nothing to get subtly wrong. The script
extracts `_HOME_CSS`, `_duration` and `_render_home` from `serve.py`, writes them to
`web.py` with the two public ones renamed, and deletes them from `serve.py`.

```bash
cd /Users/matias/Documents/repos/HomeScreen
python3 - <<'EOF'
import re
src = open("homescreen/serve.py").read()

# Each symbol runs from its definition to the next top-level def.
def cut(name, pattern):
    global src
    m = re.search(pattern, src, re.S)
    assert m, f"could not find {name}"
    src = src.replace(m.group(0), "")
    return m.group(0).rstrip() + "\n"

css   = cut("_HOME_CSS",    r'\n_HOME_CSS = """.*?"""\n')
dur   = cut("_duration",    r'\ndef _duration\(seconds: float\) -> str:\n.*?\n(?=\n_HOME_CSS|\ndef |\n@)')
rend  = cut("_render_home", r'\ndef _render_home\(st: dict\) -> str:\n.*?\n(?=\ndef |\n@)')

open("homescreen/web.py", "w").write(
    '"""HTML rendering for the human-facing pages.\n\n'
    "Split out of serve.py, which had grown to 492 lines across five concerns.\n"
    "The fleet dashboard grows this file further; keeping markup out of the\n"
    'routing module means neither has to be read to understand the other.\n"""\n\n'
    "from __future__ import annotations\n\nimport html\n\n"
    + css
    + dur.replace("def _duration(", "def duration(")
    + rend.replace("def _render_home(", "def render_home(")
          .replace("_duration(", "duration(")
)

src = src.replace("import html\n", "", 1)
src = src.replace("from homescreen import overrides",
                  "from homescreen import overrides, web", 1)
src = src.replace("_render_home(_status())", "web.render_home(_status())")
open("homescreen/serve.py", "w").write(src)
print("moved: _HOME_CSS, _duration -> duration, _render_home -> render_home")
EOF
venv/bin/python -c "import homescreen.web, homescreen.serve; print('both modules import')"
```

Expected: `moved: ...` then `both modules import`. If the script asserts, the symbols
have moved since this plan was written — do the move by hand and keep Step 3's
byte-identical check, which is what actually guarantees correctness.

- [ ] **Step 3: Verify the output is byte-identical and the suite is unchanged**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/python - <<'EOF'
from pathlib import Path
from homescreen.serve import create_app
cfg = {"feeds": {"adsb": {"source": "api", "endpoint": "https://x", "fetch_seconds": 3}},
       "devices": [{"id": "radar", "kind": "gc9a01_client", "render": "device",
                    "feed": "adsb", "poll_seconds": 5}]}
c = create_app(cfg, Path("/tmp/baseline-cache"), version="baseline").test_client()
after = c.get("/home").get_data(as_text=True)
before = Path("/tmp/home-before.html").read_text()
assert after == before, "refactor changed the output; it must not"
print("PASS: byte-identical output")
EOF
venv/bin/pytest -q
```

Expected: `PASS: byte-identical output`, then the full suite green with the **same count as before this task**.

- [ ] **Step 4: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/web.py homescreen/serve.py
git commit -m "Move HTML rendering out of serve.py into web.py"
```

---

## Task 3: Device management endpoints

**Runs on: Mac**

**Files:**
- Modify: `homescreen/serve.py` — add four routes
- Test: `tests/test_devices_api.py`

**Interfaces:**
- Consumes: `registry.load/assign/forget/is_online/KNOWN_SCENES` (Task 1)
- Produces: `GET /api/devices`, `GET /api/devices/<hw>`, `PATCH /api/devices/<hw>`, `DELETE /api/devices/<hw>`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_devices_api.py
from pathlib import Path

import pytest

from homescreen import registry
from homescreen.serve import create_app

HW = "a4cf12ab3c44"
CFG = {"feeds": {"adsb": {"source": "api", "endpoint": "https://x",
                          "fetch_seconds": 3}},
       "devices": []}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def ctx(tmp_path):
    clock = Clock()
    app = create_app(CFG, tmp_path, clock=clock, version="t")
    return app.test_client(), tmp_path, clock


def test_empty_fleet_lists_nothing(ctx):
    client, _, _ = ctx
    assert client.get("/api/devices").get_json()["devices"] == []


def test_fleet_list_reports_identity_state_and_assignment(ctx):
    client, cache, clock = ctx
    registry.touch(cache, HW, fw="0.2.0", caps={"w": 240}, now=clock.t)
    registry.assign(cache, HW, name="radar", scene="planes")
    dev = client.get("/api/devices").get_json()["devices"][0]
    assert dev["hw"] == HW
    assert dev["name"] == "radar"
    assert dev["scene"] == "planes"
    assert dev["fw"] == "0.2.0"
    assert dev["online"] is True


def test_a_silent_device_shows_offline(ctx):
    client, cache, clock = ctx
    registry.touch(cache, HW, now=clock.t)
    clock.t += 3600
    assert client.get("/api/devices").get_json()["devices"][0]["online"] is False


def test_patch_sets_name_and_scene(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    r = client.patch(f"/api/devices/{HW}", json={"name": "radar", "scene": "planes"})
    assert r.status_code == 200
    assert registry.load(cache)[HW]["name"] == "radar"
    assert registry.load(cache)[HW]["scene"] == "planes"


@pytest.mark.parametrize("body", [
    {"scene": "no-such-scene"},
    {"name": "has/slash"},
    {"name": ""},
    {"poll_seconds": 0},
    {"fw": "1.0"},          # reported by the device, not settable by a human
    {"caps": {}},           # same
    {"last_seen": "now"},   # same
])
def test_invalid_patches_are_rejected_and_persist_nothing(ctx, body):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    before = registry.load(cache)[HW]
    r = client.patch(f"/api/devices/{HW}", json=body)
    assert r.status_code == 400
    assert registry.load(cache)[HW] == before


def test_patch_on_an_unknown_device_is_404(ctx):
    client, _, _ = ctx
    assert client.patch("/api/devices/nope", json={"name": "x"}).status_code == 404


def test_delete_forgets_a_retired_board(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, now=1000.0)
    assert client.delete(f"/api/devices/{HW}").status_code == 200
    assert registry.load(cache) == {}
    assert client.delete(f"/api/devices/{HW}").status_code == 404


def test_get_one_device_returns_the_full_record(ctx):
    client, cache, _ = ctx
    registry.touch(cache, HW, fw="0.2.0", caps={"w": 240}, now=1000.0)
    body = client.get(f"/api/devices/{HW}").get_json()
    assert body["caps"] == {"w": 240}
    assert body["first_seen"] and body["last_seen"]
    assert client.get("/api/devices/nope").status_code == 404


def test_known_scenes_are_advertised_so_a_client_can_offer_them(ctx):
    client, _, _ = ctx
    assert set(client.get("/api/devices").get_json()["scenes"]) == set(registry.KNOWN_SCENES)


def test_a_corrupt_registry_does_not_500_the_fleet_view(ctx):
    client, cache, _ = ctx
    registry.registry_path(cache).parent.mkdir(parents=True, exist_ok=True)
    registry.registry_path(cache).write_text("{not json")
    assert client.get("/api/devices").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_devices_api.py -q
```

Expected: FAIL — 404s, because the routes do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add to `homescreen/serve.py`, immediately before the `@app.get("/api/status")` route:

```python
    # Reported by the device on every call, never set by a human. Listing them
    # explicitly means a typo in a PATCH is a 400 rather than a silent write
    # that corrupts the device's own view of itself.
    _DEVICE_READONLY = ("fw", "caps", "telemetry", "first_seen", "last_seen")

    def _fleet_entry(hw: str, rec: dict, now: float) -> dict:
        return {"hw": hw, "name": rec.get("name"), "scene": rec.get("scene"),
                "fw": rec.get("fw"), "poll_seconds": rec.get("poll_seconds"),
                "online": registry.is_online(rec, now),
                "last_seen": rec.get("last_seen"),
                "first_seen": rec.get("first_seen"),
                "caps": rec.get("caps", {}),
                "telemetry": rec.get("telemetry", {})}

    @app.get("/api/devices")
    def list_devices():
        now = clock()
        data = registry.load(cache_dir)
        return jsonify({
            "scenes": list(registry.KNOWN_SCENES),
            "devices": [_fleet_entry(hw, rec, now)
                        for hw, rec in sorted(data.items())],
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
        readonly = [k for k in body if k in _DEVICE_READONLY]
        if readonly:
            return jsonify({"error": f"device-reported, not settable: "
                                     f"{sorted(readonly)}"}), 400
        unknown = [k for k in body
                   if k not in ("name", "scene", "poll_seconds")]
        if unknown:
            return jsonify({"error": f"not settable: {sorted(unknown)}",
                            "settable": ["name", "scene", "poll_seconds"]}), 400
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
```

And add the import at the top of `serve.py`:

```python
from homescreen import overrides, registry, web
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_devices_api.py -q
```

Expected: PASS — **17 tests**

- [ ] **Step 5: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/serve.py tests/test_devices_api.py
git commit -m "Add device management endpoints"
```

---

## Task 4: The device call, with self-registration

**Runs on: Mac**

**Files:**
- Modify: `homescreen/serve.py` — add one route
- Test: `tests/test_devices_api.py` — extend

**Interfaces:**
- Consumes: `registry.touch` (Task 1)
- Produces: `GET /api/device/<hw>/scene`

> **This is the only endpoint a device calls.** In Phase 1 it returns the assignment; Phase 2 fills in `components`. Keeping the shape now means Phase 2 changes the payload, not the contract.
>
> Note the path is `/api/device/` (singular) for the device-facing call and `/api/devices/` (plural) for management. That is deliberate — they have different audiences and different stability requirements, and a device should never accidentally hit a management route.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_devices_api.py


def test_first_contact_registers_and_says_it_is_unassigned(ctx):
    client, cache, _ = ctx
    r = client.get(f"/api/device/{HW}/scene?fw=0.2.0&rssi=-64&uptime=99")
    assert r.status_code == 200
    body = r.get_json()
    assert body["assigned"] is False
    assert body["scene"] == "unassigned"
    assert body["hw"] == HW, "so a newly flashed board can tell you its id"
    rec = registry.load(cache)[HW]
    assert rec["fw"] == "0.2.0"
    assert rec["telemetry"]["rssi"] == "-64"


def test_an_assigned_device_gets_its_scene(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene")
    registry.assign(cache, HW, name="radar", scene="planes")
    body = client.get(f"/api/device/{HW}/scene").get_json()
    assert body["assigned"] is True
    assert body["scene"] == "planes"
    assert body["name"] == "radar"


def test_the_device_call_updates_last_seen_and_liveness(ctx):
    client, cache, clock = ctx
    client.get(f"/api/device/{HW}/scene")
    clock.t += 3600
    assert client.get("/api/devices").get_json()["devices"][0]["online"] is False
    client.get(f"/api/device/{HW}/scene")
    assert client.get("/api/devices").get_json()["devices"][0]["online"] is True


def test_poll_seconds_is_advertised_so_cadence_stays_server_controlled(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene")
    registry.assign(cache, HW, poll_seconds=30)
    r = client.get(f"/api/device/{HW}/scene")
    assert r.headers["X-Poll-Seconds"] == "30"


def test_capabilities_are_recorded_from_the_query_string(ctx):
    client, cache, _ = ctx
    client.get(f"/api/device/{HW}/scene?w=240&h=240&depth=16&layouts=fill"
               "&components=text,rings,markers")
    caps = registry.load(cache)[HW]["caps"]
    assert caps["w"] == 240 and caps["h"] == 240 and caps["depth"] == 16
    assert caps["layouts"] == ["fill"]
    assert caps["components"] == ["text", "rings", "markers"]


@pytest.mark.parametrize("hw", ["", "   ", "has/slash", "x" * 200])
def test_an_unusable_hardware_id_is_rejected_not_registered(ctx, hw):
    client, cache, _ = ctx
    r = client.get(f"/api/device/{hw}/scene")
    assert r.status_code in (400, 404)
    assert registry.load(cache) == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_devices_api.py -q -k "device_call or first_contact or assigned_device or capabilities or unusable"
```

Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Write minimal implementation**

Add to `homescreen/serve.py`, before `@app.get("/api/status")`:

```python
    _CAP_INTS = ("w", "h", "depth")
    _CAP_LISTS = ("layouts", "components")

    def _caps_from_query(args) -> dict:
        """Capabilities ride on every call rather than a separate handshake, so
        a server restart cannot lose them and there is no registration state
        machine to get wrong."""
        caps = {}
        for key in _CAP_INTS:
            try:
                caps[key] = int(args[key])
            except (KeyError, TypeError, ValueError):
                pass
        for key in _CAP_LISTS:
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip():
                caps[key] = [p for p in (s.strip() for s in raw.split(",")) if p]
        return caps

    @app.get("/api/device/<hw>/scene")
    def device_scene(hw: str):
        args = request.args.to_dict()
        caps = _caps_from_query(args)
        telemetry = {k: v for k, v in args.items()
                     if k not in _CAP_INTS + _CAP_LISTS + ("fw",)}
        try:
            rec = registry.touch(cache_dir, hw, fw=args.get("fw"),
                                 caps=caps or None,
                                 telemetry=telemetry or None, now=clock())
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        assigned = rec.get("scene") not in (None, "unassigned")
        body = {"hw": hw, "name": rec.get("name"),
                "scene": rec.get("scene") or "unassigned",
                "assigned": assigned}
        if not assigned:
            # A newly flashed board should be able to tell you what to type
            # into the fleet view, rather than sitting blank.
            body["message"] = "not assigned — set a scene in the fleet view"
        resp = jsonify(body)
        resp.headers["X-Poll-Seconds"] = str(
            int(rec.get("poll_seconds") or registry.DEFAULT_POLL_SECONDS))
        return resp
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_devices_api.py -q
```

Expected: PASS — **23 tests**

- [ ] **Step 5: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/serve.py tests/test_devices_api.py
git commit -m "Add the device-facing scene endpoint with self-registration"
```

---

## Task 5: Migration from config, and the friendly-name alias

**Runs on: Mac**

**Files:**
- Modify: `homescreen/registry.py` — add `seed_from_config`
- Modify: `homescreen/serve.py` — seed at startup; resolve names in `_lookup`
- Modify: `config.yaml`, `config.example.yaml` — add `scene: planes` to the radar entry
- Test: `tests/test_registry.py`, `tests/test_devices_api.py` — extend

**Interfaces:**
- Consumes: `registry.load/save` (Task 1), `config.device` (existing)
- Produces: `registry.seed_from_config(cfg: dict, cache_dir: Path) -> int` — returns how many were seeded

> **Why the seeded id is synthetic.** `config.yaml` names a device but cannot know which physical board it meant. Seeding under `cfg:radar` rather than guessing a hardware id means that when the real board self-registers it appears as a second, unassigned device — and adopting it is a deliberate rename, not a silent merge on a guess. (spec §4.7)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_registry.py


CFG_WITH_DEVICE = {"devices": [
    {"id": "radar", "kind": "gc9a01_client", "render": "device",
     "feed": "adsb", "poll_seconds": 5, "scene": "planes"}]}


def test_seeding_carries_the_name_and_scene_from_config(tmp_path: Path):
    assert registry.seed_from_config(CFG_WITH_DEVICE, tmp_path) == 1
    rec = registry.load(tmp_path)["cfg:radar"]
    assert rec["name"] == "radar"
    assert rec["scene"] == "planes"
    assert rec["poll_seconds"] == 5
    assert rec["fw"] == "config"


def test_seeding_is_idempotent_and_never_clobbers_a_live_record(tmp_path: Path):
    registry.seed_from_config(CFG_WITH_DEVICE, tmp_path)
    registry.assign(tmp_path, "cfg:radar", scene="unassigned")
    assert registry.seed_from_config(CFG_WITH_DEVICE, tmp_path) == 0
    assert registry.load(tmp_path)["cfg:radar"]["scene"] == "unassigned"


def test_seeding_defaults_to_unassigned_when_config_names_no_scene(tmp_path: Path):
    registry.seed_from_config({"devices": [{"id": "x", "render": "device"}]}, tmp_path)
    assert registry.load(tmp_path)["cfg:x"]["scene"] == "unassigned"


def test_seeding_survives_a_malformed_devices_list(tmp_path: Path):
    for cfg in ({}, {"devices": None}, {"devices": "radar"},
                {"devices": [None, 5, {"no_id": 1}]}):
        assert registry.seed_from_config(cfg, tmp_path) == 0
    assert registry.load(tmp_path) == {}
```

```python
# append to tests/test_devices_api.py


def test_the_friendly_alias_reaches_a_registry_device(tmp_path):
    # A device that exists ONLY in the registry must be reachable by name, or
    # the fleet view would show a device you cannot curl.
    clock = Clock()
    app = create_app(CFG, tmp_path, clock=clock, version="t")
    client = app.test_client()
    client.get(f"/api/device/{HW}/scene")
    registry.assign(tmp_path, HW, name="kitchen", scene="planes")
    assert client.get("/api/display/kitchen/health").status_code == 200


def test_a_config_device_still_wins_its_own_name(tmp_path):
    # The live deployment serves /api/display/radar/data from config.yaml.
    # Resolution must try config FIRST so this cannot regress.
    cfg = {"feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
           "devices": [{"id": "radar", "kind": "gc9a01_client",
                        "render": "device", "feed": "adsb", "poll_seconds": 5}]}
    client = create_app(cfg, tmp_path, version="t").test_client()
    assert client.get("/api/display/radar/data").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_registry.py tests/test_devices_api.py -q
```

Expected: FAIL — `AttributeError: module 'homescreen.registry' has no attribute 'seed_from_config'`

- [ ] **Step 3: Add `seed_from_config` to `homescreen/registry.py`**

```python
def seed_from_config(cfg: dict, cache_dir: Path) -> int:
    """Seed registry records from any devices still declared in config.yaml.

    Returns how many were newly created. Idempotent, and never overwrites an
    existing record -- re-running this must not undo an assignment made in the
    fleet view.

    The id is synthetic (`cfg:<id>`) because config.yaml names a device but
    cannot know which physical board it meant. When the real board registers
    under its true hardware id it appears separately, and adopting it is a
    deliberate rename rather than a silent merge on a guess.
    """
    devices = cfg.get("devices")
    if not isinstance(devices, list):
        return 0
    data = load(cache_dir)
    seeded = 0
    stamp = _now_iso()
    for dev in devices:
        if not isinstance(dev, dict) or not isinstance(dev.get("id"), str):
            continue
        hw_id = f"cfg:{dev['id']}"
        if hw_id in data:
            continue
        scene = dev.get("scene")
        data[hw_id] = {
            "name": dev["id"],
            "scene": scene if scene in KNOWN_SCENES else "unassigned",
            "first_seen": stamp, "last_seen": stamp,
            "poll_seconds": dev.get("poll_seconds") or DEFAULT_POLL_SECONDS,
            "fw": "config", "caps": {}, "telemetry": {},
        }
        seeded += 1
    if seeded:
        save(cache_dir, data)
        log.info("seeded %d device(s) from config.yaml", seeded)
    return seeded
```

- [ ] **Step 4: Seed at startup and resolve names in `serve.py`**

In `create_app`, immediately after `started_at = clock()`:

```python
    registry.seed_from_config(cfg, cache_dir)
```

And extend `_lookup` so the friendly alias also reaches registry-only devices.
Replace the existing body with:

```python
    def _lookup(device_id: str, *, require_data_render: bool = True):
        """`/data` is data-push only; `/health` answers for every device.

        Config is tried FIRST so the live `/api/display/radar/data` route
        cannot regress while both sources coexist during migration.
        """
        cfg_live = _live()
        dev = device(cfg_live, device_id)
        if dev is None:
            hw = registry.resolve_name(cache_dir, device_id)
            if hw is None:
                return None
            rec = registry.load(cache_dir).get(hw, {})
            dev = {"id": device_id, "render": "device", "feed": "adsb",
                   "kind": (rec.get("caps") or {}).get("kind"),
                   "poll_seconds": rec.get("poll_seconds")}
        if require_data_render and dev.get("render") != DATA_RENDER:
            return None
        return dev
```

- [ ] **Step 5: Add the scene key to both config files**

```bash
cd /Users/matias/Documents/repos/HomeScreen
python3 - <<'EOF'
for f in ("config.yaml", "config.example.yaml"):
    s = open(f).read()
    s = s.replace("    max_aircraft: 40\n",
                  "    max_aircraft: 40\n    scene: planes\n")
    open(f, "w").write(s)
    print("  added scene: planes to", f)
EOF
grep -n "scene:" config.yaml
```

- [ ] **Step 6: Run the tests**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest -q
```

Expected: PASS — the whole suite, with the registry and device-API files now at
**23** and **25** tests.

- [ ] **Step 7: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/registry.py homescreen/serve.py config.yaml config.example.yaml \
        tests/test_registry.py tests/test_devices_api.py
git commit -m "Seed the registry from config and resolve friendly names"
```

---

## Task 6: Fleet dashboard

**Runs on: Mac**

**Files:**
- Modify: `homescreen/web.py` — add a devices section
- Modify: `homescreen/serve.py` — put devices into `_status()`
- Test: `tests/test_home.py` — extend

**Interfaces:**
- Consumes: `registry.load/is_online` (Task 1), `web.render_home` (Task 2)
- Produces: the fleet section of `/` and a `fleet` key in `/api/status`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_home.py

from homescreen import registry


def test_home_lists_registered_devices_with_state(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    clock = Clock()
    monkeypatch.setattr(
        "homescreen.cache._now_iso",
        lambda: datetime.fromtimestamp(clock.t, timezone.utc).isoformat())
    app = create_app(CFG, tmp_path, clock=clock, version="abc1234")
    client = app.test_client()
    registry.touch(tmp_path, "a4cf12ab3c44", fw="0.2.0", now=clock.t)
    registry.assign(tmp_path, "a4cf12ab3c44", name="radar", scene="planes")
    registry.touch(tmp_path, "deadbeef0000", fw="0.1.0", now=clock.t)

    body = client.get("/home").get_data(as_text=True)
    assert "radar" in body and "planes" in body
    assert "a4cf12ab3c44" in body, "hw id shown so a new board is identifiable"
    assert "unassigned" in body, "the second board has no scene yet"
    assert "0.2.0" in body and "0.1.0" in body, "firmware versions"


def test_home_marks_a_silent_device_offline(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    clock = Clock()
    monkeypatch.setattr(
        "homescreen.cache._now_iso",
        lambda: datetime.fromtimestamp(clock.t, timezone.utc).isoformat())
    client = create_app(CFG, tmp_path, clock=clock, version="v").test_client()
    registry.touch(tmp_path, "a4cf12ab3c44", now=clock.t)
    assert "offline" not in client.get("/home").get_data(as_text=True).lower()
    clock.t += 3600
    assert "offline" in client.get("/home").get_data(as_text=True).lower()


def test_status_json_carries_the_fleet(tmp_path):
    client = create_app(CFG, tmp_path, version="v").test_client()
    registry.touch(tmp_path, "a4cf12ab3c44", fw="0.2.0", now=1000.0)
    fleet = client.get("/api/status").get_json()["fleet"]
    assert fleet[0]["hw"] == "a4cf12ab3c44"
    assert fleet[0]["scene"] == "unassigned"


def test_home_renders_with_an_empty_fleet(tmp_path):
    client = create_app(CFG, tmp_path, version="v").test_client()
    body = client.get("/home").get_data(as_text=True)
    assert client.get("/home").status_code == 200
    assert "no devices" in body.lower()


def test_home_does_not_500_on_a_corrupt_registry(tmp_path):
    registry.registry_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    registry.registry_path(tmp_path).write_text("{not json")
    client = create_app(CFG, tmp_path, version="v").test_client()
    assert client.get("/home").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest tests/test_home.py -q
```

Expected: FAIL — `KeyError: 'fleet'`, and the device names are absent from the page.

- [ ] **Step 3: Add the fleet to `_status()` in `serve.py`**

Inside `_status()`, add a `fleet` key to the returned dict:

```python
            "fleet": [_fleet_entry(hw, rec, now)
                      for hw, rec in sorted(registry.load(cache_dir).items())],
```

- [ ] **Step 4: Render the fleet section in `web.py`**

In `render_home`, immediately before the `<footer>` line, insert a devices section
built from `st["fleet"]`:

```python
    fleet_rows = []
    for d in st.get("fleet", []):
        state = ('<span class="ok">online</span>' if d["online"]
                 else '<span class="bad">offline</span>')
        name = e(d["name"]) if d["name"] else '<span class="tag">unnamed</span>'
        scene = e(d["scene"] or "unassigned")
        fleet_rows.append(f"""<div class="card">
  <div class="row"><span class="name">{name}</span>
    <span class="tag">{e(d["hw"])}</span>
    <span class="tag">scene: {scene}</span>
    <span class="tag">fw {esc(d["fw"])}</span>
    <span class="tag">poll {esc(d["poll_seconds"])}s</span>
    {state}</div>
  <dl><dt>last seen</dt><dd>{esc(d["last_seen"])}</dd>
      <dt>first seen</dt><dd>{esc(d["first_seen"])}</dd></dl>
</div>""")
    fleet_html = ("<h2>Fleet</h2>"
                  + ("".join(fleet_rows)
                     or '<div class="card">no devices have called in yet</div>'))
```

and interpolate `{fleet_html}` into the returned template immediately above the
`<footer>` element.

- [ ] **Step 5: Run the tests**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/pytest -q
```

Expected: PASS — the whole suite.

- [ ] **Step 6: Look at the page before committing**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/python - <<'EOF'
from pathlib import Path
from homescreen import registry
from homescreen.serve import create_app
cache = Path("/tmp/fleet-demo"); cache.mkdir(exist_ok=True)
registry.save(cache, {})
cfg = {"feeds": {"adsb": {"source": "api", "endpoint": "https://x", "fetch_seconds": 3}},
       "devices": []}
c = create_app(cfg, cache, version="demo").test_client()
registry.touch(cache, "a4cf12ab3c44", fw="0.2.0")
registry.assign(cache, "a4cf12ab3c44", name="radar", scene="planes")
registry.touch(cache, "deadbeef0000", fw="0.1.0")
Path("/tmp/fleet.html").write_text(c.get("/home").get_data(as_text=True))
print("wrote /tmp/fleet.html")
EOF
open /tmp/fleet.html
```

Confirm the fleet section reads clearly: one named+assigned device, one unnamed
unassigned device, both showing firmware and last-seen.

- [ ] **Step 7: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/web.py homescreen/serve.py tests/test_home.py
git commit -m "Add the fleet dashboard"
```

---

## Task 7: Deploy and verify against the live Pi

**Runs on: Mac.** Every Pi-side command is prefixed `ssh pi@192.168.1.116`.

> **Use the IP, not `dashboard.local`.** mDNS resolution from this Mac has been
> intermittently failing while the Pi's own `avahi-daemon` is healthy.

- [ ] **Step 1: Push and deploy**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git push origin main
ssh pi@192.168.1.116 'cd /home/pi/dashboard && git fetch -q origin && git reset --hard -q origin/main && venv/bin/pytest -q 2>&1 | tail -1'
```

Expected: the same test count as the Mac, on Python 3.13.

- [ ] **Step 2: Restart and confirm the live endpoint did not regress**

```bash
ssh pi@192.168.1.116 'sudo systemctl restart homescreen-serve && sleep 3 && systemctl is-active homescreen-serve'
curl -s -o /dev/null -w "  /api/display/radar/data -> %{http_code}\n" \
  http://192.168.1.116:8080/api/display/radar/data
```

Expected: `active`, then **200** — the live route must survive the migration.

- [ ] **Step 3: Confirm the config device was seeded**

```bash
curl -s http://192.168.1.116:8080/api/devices | python3 -m json.tool
```

Expected: one device, `hw: "cfg:radar"`, `name: "radar"`, `scene: "planes"`, `fw: "config"`.

- [ ] **Step 4: Simulate a new board self-registering**

```bash
curl -s "http://192.168.1.116:8080/api/device/a4cf12ab3c44/scene?fw=0.2.0&rssi=-58&w=240&h=240&depth=16&layouts=fill&components=text,rings,markers" \
  | python3 -m json.tool
```

Expected: `assigned: false`, `scene: "unassigned"`, and the hardware id echoed back.

- [ ] **Step 5: Adopt it from the server**

```bash
curl -s -X PATCH http://192.168.1.116:8080/api/devices/a4cf12ab3c44 \
  -H 'Content-Type: application/json' -d '{"name":"radar-new","scene":"planes"}' \
  | python3 -m json.tool
curl -s "http://192.168.1.116:8080/api/device/a4cf12ab3c44/scene" \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print('  now:',d['name'],'->',d['scene'],'| assigned',d['assigned'])"
```

Expected: the device's next call returns `planes` — assignment from the server, no reflash.

- [ ] **Step 6: Confirm offline detection and clean up**

```bash
curl -s http://192.168.1.116:8080/api/devices \
  | python3 -c "
import json,sys
for d in json.load(sys.stdin)['devices']:
    print(f\"  {d['hw']:16} {d['name'] or '-':12} {d['scene']:12} online={d['online']}\")"
curl -s -X DELETE http://192.168.1.116:8080/api/devices/a4cf12ab3c44 >/dev/null
echo "  test device forgotten"
```

Expected: `cfg:radar` online (it was just seeded), the simulated board online, and after the
DELETE only `cfg:radar` remains.

- [ ] **Step 7: Look at the fleet dashboard**

```bash
open http://192.168.1.116:8080/
```

Confirm the fleet section renders with the seeded device, its scene and firmware.

---

## Done when

- [ ] `venv/bin/pytest` green on the Mac **and** on the Pi, same count
- [ ] `/api/display/radar/data` still returns 200 — no regression to the live deployment
- [ ] An unknown hardware id self-registers on first call and reports `assigned: false`
- [ ] A `PATCH` to `/api/devices/{hw}` changes what that device's next call returns
- [ ] A device silent for 3× its poll interval shows `online: false`
- [ ] The config device appears as `cfg:radar` and its assignment survives a restart
- [ ] A corrupt `cache/devices.json` leaves `/`, `/api/devices` and `/api/status` at 200
- [ ] The fleet dashboard shows name, hw id, scene, firmware, online state and last seen

**Then** Phase 2 (`scenes`) can be planned — with a real `caps` payload from a real device
to design against, which is why it was not planned alongside this.
