# Radar Data Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve pre-computed ADS-B aircraft data from the Pi at `/api/display/radar/data`, so the ESP32-C3 plane radar fetches from the LAN over plain HTTP instead of calling `adsb.fi` directly over TLS.

**Architecture:** A looping daemon (`homescreen/sources/adsb.py`) polls adsb.fi on a cadence read from config and writes a cache envelope to `cache/feed/radar.json` (one per device). A second always-on daemon (`homescreen/serve.py`) reads that cache and renders a compact JSON response, **recomputing each aircraft's position age at serve time** so the device's dead reckoning stays correct across the cache hop. Device polling and upstream fetching are fully decoupled: `serve.py` never makes a network call.

**Tech Stack:** Python 3.13 (Pi) / 3.14 (Mac), Flask, requests, PyYAML, pytest. No numpy.

**Spec:** `docs/PLAN.md` §3 (endpoint schema and the `pos_age_s` trap), `docs/SPEC.md` §7 (cache envelope) and §11 (failure rules), `docs/ADDENDUM-01-multi-display.md` §5 (HTTP protocol) and §6 (config), `docs/VALIDATION-01.md` F4 (why serve-time age matters) and C1/C6/C7.

**Scope:** Server side only (Phase A1–A3). The firmware change (A4–A5) is a separate plan in the sibling repo `/Users/matias/Documents/repos/ESP32-Plane-Radar`, and per ADDENDUM §0.5 must not start until this endpoint is proven end-to-end.

## Machine convention

**Every step is labelled `Runs on: Mac` or `Runs on: Pi`.** The Mac is the dev machine; the Pi (`ssh pi@dashboard.local`) is a deploy target (CLAUDE.md §5). Tasks 0–5 are Mac-only. Task 6 deploys.

Deploy path on the Pi is **`/home/pi/dashboard`** (CLAUDE.md §4, SPEC §12). Never `/home/pi/homescreen`.

## Global Constraints

- **Cache envelope is fixed**: `{"fetched_at": <ISO8601 with offset>, "ok": <bool>, "error": <str|null>, "data": {...}}`. Enforced on **read as well as write**. (SPEC §7)
- **Nothing may raise into the serve path.** A corrupt cache degrades to "no data", never a traceback. (SPEC §11.1)
- **`serve.py` performs no network I/O.** Ever. (VALIDATION C7)
- **`config.yaml` is the single source of truth for cadence.** `X-Poll-Seconds` and the fetch loop interval both read from it; no cadence literal may live in a systemd unit. (VALIDATION C6)
- Plain HTTP, no TLS. (ADDENDUM §5)
- ADDENDUM §5's `X-Full-Refresh` and VALIDATION C3's `X-Partial-Window` are **deliberately
  absent**: both are pixel-push anti-ghosting concerns and a GC9A01 has no ghosting.
  They land with `/frame` in Phase C (PLAN.md §5, S1).
- venv created with `--system-site-packages`. Never `pip --break-system-packages`. (CLAUDE.md §2)
- String field widths are fixed by the firmware struct: `cs` ≤ 8, `ty` ≤ 4, `alt` ≤ 11.
- Upstream `https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{nm}` → `{"ac": [...]}`. Public limit **1 req/s**.
- **`dst` is nautical miles**; `radius_km` is kilometres. 1 NM = 1.852 km. Any comparison between them must convert.
- **An empty sky is `[]` and nothing else.** A response whose `ac` is missing, `null`, a
  number or an HTML page is *not* this API — it is a failure, and the last good list must
  survive. Treating it as "no traffic" wipes real aircraft off the panel, and moving the
  fetch to the Pi **removes the device's own guard** against it. See Task 3.
- **Cache file is `cache/feed/{device_id}.json`** → `radar.json`, exactly as SPEC,
  ADDENDUM §7, PLAN.md §2 A2 and VALIDATION C7 all say. Keyed by **device**, not feed,
  because `home`/`radius_km`/`max_aircraft` are per-device display parameters
  (ADDENDUM §6 puts them on the device entry): two radars with different centres need
  different caches, so a feed-keyed file could not serve both.
- **Every Mac code block begins with `cd /Users/matias/Documents/repos/HomeScreen`.**
  Tasks are executed by separate subagents whose shell cwd resets between calls.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | pytest `pythonpath` so `import homescreen` resolves from the repo root. |
| `homescreen/cache.py` | Cache envelope read/write with **strict validation on read**. |
| `homescreen/config.py` | Load config, look up a device, resolve its feed. |
| `homescreen/sources/adsb_map.py` | **Pure** mapping of one raw adsb.fi record. No I/O, no clock. |
| `homescreen/sources/adsb.py` | Fetch, radius-filter, map, cache. Owns network and clock. Loops. |
| `homescreen/serve.py` | Flask app: `/data` and `/health`. Reads cache, recomputes age, sets headers. |
| `tests/test_cache.py` | Envelope round-trip, failure preservation, malformed-shape rejection. |
| `tests/test_adsb_map.py` | Fallback chains against a pinned real record. |
| `tests/test_adsb.py` | Radius filter, cap, failure handling. |
| `tests/test_serve.py` | Serve-time age (end-to-end through the real ISO parse), ETag, degraded paths. |
| `config.yaml` | Device registry and feed config. **Committed** — it is structure, not secrets. |
| `config.local.yaml` | Secrets only. Already gitignored. |
| `systemd/homescreen-serve.service`, `systemd/homescreen-fetch.service` | Two `Type=simple` daemons. **No timer** — cadence lives in config. |

---

## Task 0: Workspace setup

**Runs on: Mac**

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `config.yaml`, `config.example.yaml`
- Create: `homescreen/__init__.py`, `homescreen/sources/__init__.py`, `tests/__init__.py`

**Interfaces:**
- Consumes: nothing
- Produces: a venv at `venv/` with pytest, flask, requests, pyyaml; an importable `homescreen` package.

> **Why this task exists:** every later task runs `venv/bin/pytest`, and pytest's default
> `prepend` import mode will not put the repo root on `sys.path` without either a
> `tests/__init__.py` or a `pythonpath` setting. Without both this task creates, Task 1's
> tests fail with `ModuleNotFoundError: No module named 'homescreen'` — which is
> indistinguishable from the failure Task 1 Step 2 *wants* to see.

- [ ] **Step 1: Create the package skeleton**

```bash
cd /Users/matias/Documents/repos/HomeScreen
mkdir -p homescreen/sources tests/fixtures cache/feed systemd
touch homescreen/__init__.py homescreen/sources/__init__.py tests/__init__.py
```

- [ ] **Step 2: Create `pyproject.toml`**

```bash
cd /Users/matias/Documents/repos/HomeScreen
cat > pyproject.toml <<'EOF'
[tool.pytest.ini_options]
# pytest's prepend import mode does not add the rootdir to sys.path on its own.
pythonpath = ["."]
testpaths = ["tests"]
EOF
```

- [ ] **Step 3: Create the venv and install dependencies**

```bash
cd /Users/matias/Documents/repos/HomeScreen
cat > requirements.txt <<'EOF'
flask>=3.0
requests>=2.31
pyyaml>=6.0
pytest>=8.0
EOF
python3 -m venv --system-site-packages venv
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt
venv/bin/pytest --version
```

Expected: a version line — `pytest 9.x.x` today. Any `>= 8` is fine; the
pin is `pytest>=8.0`, so do not treat a higher version as a failure.

- [ ] **Step 4: Create the config**

```bash
cd /Users/matias/Documents/repos/HomeScreen
cat > config.example.yaml <<'EOF'
# Copy to config.yaml. Structure lives here and IS committed; secrets go in
# config.local.yaml, which is gitignored.
server:
  host: 0.0.0.0
  port: 8080

devices:
  - id: radar
    kind: gc9a01_client
    render: device
    feed: adsb
    home: { lat: 40.4168, lon: -3.7038 }
    radius_km: 60
    max_aircraft: 20
    show_ground: false
    poll_seconds: 5

feeds:
  adsb:
    source: api
    endpoint: "https://opendata.adsb.fi/api/v3"
    # Upstream cadence. adsb.fi's public limit is 1 req/s; keep well clear.
    # This is the ONLY place the fetch interval is defined (VALIDATION C6).
    fetch_seconds: 3
EOF
cp config.example.yaml config.yaml
```

- [ ] **Step 5: Verify the skeleton imports and commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/python -c "import homescreen, homescreen.sources; print('package ok')"
git add pyproject.toml requirements.txt config.yaml config.example.yaml \
        homescreen/__init__.py homescreen/sources/__init__.py tests/__init__.py
git commit -m "Add Python workspace, dependencies and device config"
```

---

## Task 1: Cache envelope

**Runs on: Mac**

**Files:**
- Create: `homescreen/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `write_cache(path: Path, data: dict, *, ok: bool = True, error: str | None = None) -> None`
  - `read_cache(path: Path) -> dict | None` — full envelope, or `None` if absent, corrupt, **or structurally invalid**
  - `write_failure(path: Path, error: str) -> None` — preserves prior `data` and prior `fetched_at`
  - `_now_iso() -> str` — module-private, but **tests monkeypatch it**, so the name is part of the contract

> **`fetched_at` semantics.** On failure this field is deliberately left at the last
> *successful* fetch, not the attempt time. That is what makes SPEC §11.4 work
> ("the timestamp reflects the oldest successful fetch, so a silently dead fetcher is
> visible") and what makes `feed.age_s` grow during an outage. Every future fetcher
> (weather, calendar, quotes) inherits this meaning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache.py
from datetime import datetime
from pathlib import Path

import pytest

from homescreen.cache import read_cache, write_cache, write_failure


def test_write_then_read_round_trip(tmp_path: Path):
    p = tmp_path / "radar.json"
    write_cache(p, {"aircraft": [1, 2]})
    env = read_cache(p)
    assert env["ok"] is True
    assert env["error"] is None
    assert env["data"] == {"aircraft": [1, 2]}
    # Must be timezone-aware: a naive stamp would be reinterpreted as local time
    # by datetime.fromisoformat().timestamp(), a 7200 s error in Madrid CEST.
    assert datetime.fromisoformat(env["fetched_at"]).utcoffset() is not None


def test_read_missing_returns_none(tmp_path: Path):
    assert read_cache(tmp_path / "nope.json") is None


def test_read_corrupt_returns_none(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert read_cache(p) is None


@pytest.mark.parametrize("bad", [
    {},                                              # nothing at all
    {"data": {}},                                    # missing fetched_at/ok
    {"fetched_at": "x", "ok": True},                 # missing data
    {"fetched_at": "x", "ok": True, "data": []},     # data not a dict
    {"fetched_at": None, "ok": True, "data": {}},    # fetched_at not a str
    {"fetched_at": "x", "ok": "yes", "data": {}},    # ok not a bool
    [1, 2, 3],                                       # not an object
])
def test_read_rejects_structurally_invalid_envelopes(tmp_path: Path, bad):
    import json
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    assert read_cache(p) is None, f"should reject {bad!r}"


def test_failure_preserves_previous_data_and_timestamp(tmp_path: Path):
    p = tmp_path / "radar.json"
    write_cache(p, {"aircraft": [1, 2]})
    before = read_cache(p)["fetched_at"]
    write_failure(p, "boom")
    env = read_cache(p)
    assert env["ok"] is False
    assert env["error"] == "boom"
    assert env["data"] == {"aircraft": [1, 2]}, "stale data must be kept (SPEC 11.3)"
    assert env["fetched_at"] == before, "timestamp tracks last SUCCESS (SPEC 11.4)"


def test_failure_with_no_previous_cache_is_readable(tmp_path: Path):
    p = tmp_path / "fresh.json"
    write_failure(p, "boom")
    env = read_cache(p)
    assert env is not None, "must still be a valid envelope"
    assert env["ok"] is False
    assert env["data"] == {}


def test_write_refuses_to_persist_non_finite(tmp_path: Path):
    # Belt-and-braces behind adsb_map._num: bare Infinity/NaN is not strict
    # JSON, and the firmware's parser rejects the entire body over one. If a
    # future field bypasses _num, the cache must refuse rather than poison.
    p = tmp_path / "radar.json"
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError):
            write_cache(p, {"aircraft": [{"gs": bad}]})
    assert not p.exists(), "a refused write must leave no file behind"


def test_write_leaves_no_temp_file(tmp_path: Path):
    p = tmp_path / "radar.json"
    write_cache(p, {"a": 1})
    assert list(tmp_path.iterdir()) == [p]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homescreen.cache'`
(If you instead see `No module named 'homescreen'`, Task 0 was skipped — go back.)

- [ ] **Step 3: Write minimal implementation**

```python
# homescreen/cache.py
"""Cache envelope shared by every fetcher. SPEC §7 fixes this shape."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    """Timezone-aware ISO8601. Aware is load-bearing: a naive stamp is silently
    reinterpreted as local time on parse, shifting every age by the UTC offset."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def read_cache(path: Path) -> dict | None:
    """Return the envelope, or None if it is absent, corrupt or malformed.

    Never raises. Validates the full SPEC §7 shape, because everything
    downstream indexes these keys unguarded and a 500 in the serve path
    violates SPEC §11.1.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            env = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(env, dict):
        return None
    if not isinstance(env.get("fetched_at"), str):
        return None
    if not isinstance(env.get("ok"), bool):
        return None
    if not isinstance(env.get("data"), dict):
        return None
    return env


def _write(path: Path, env: dict) -> None:
    """Write atomically so a reader never sees a half-written envelope."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        # allow_nan=False: bare Infinity/NaN is not strict JSON and the
        # firmware's parser rejects the whole body. Belt-and-braces with _num.
        json.dump(env, fh, separators=(",", ":"), allow_nan=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_cache(path: Path, data: dict, *, ok: bool = True,
                error: str | None = None) -> None:
    _write(path, {"fetched_at": _now_iso(), "ok": ok, "error": error, "data": data})


def write_failure(path: Path, error: str) -> None:
    """Record a failed fetch, keeping the last good data and its timestamp."""
    prev = read_cache(path)
    if prev is not None and prev["ok"] is False and prev.get("error") == error:
        # Identical failure envelope. Rewriting it changes nothing and costs an
        # fsync every cycle -- ~28,800/day onto the microSD during an outage,
        # which is the same wear this plan rejected a systemd timer to avoid.
        return
    _write(path, {
        "fetched_at": prev["fetched_at"] if prev else _now_iso(),
        "ok": False,
        "error": error,
        "data": prev["data"] if prev else {},
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_cache.py -v`
Expected: PASS — **14 tests** (7 functions, one parametrized 7 ways)

- [ ] **Step 5: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/cache.py tests/test_cache.py
git commit -m "Add cache envelope with strict read validation and atomic writes"
```

---

## Task 2: Pure field mapping

**Runs on: Mac**

**Files:**
- Create: `homescreen/sources/adsb_map.py`
- Create: `tests/fixtures/adsb_sample.json`
- Test: `tests/test_adsb_map.py`

**Interfaces:**
- Consumes: nothing
- Produces: `map_aircraft(raw: dict, *, show_ground: bool = False) -> dict | None` — the compact record, or `None` to drop. Output keys exactly: `lat, lon, nose, trk, gs, ve, vn, age, dst, cs, ty, alt`.

> **Authority for the fallback chains.** They mirror the firmware in the sibling repo at
> `/Users/matias/Documents/repos/ESP32-Plane-Radar/src/services/adsb_client.cpp`:
> `pickNoseHeading`/`pickTrackHeading`/`pickGroundSpeed` at **117-163**,
> `isOnGround`/`copyJsonStringTrimmed`/`formatAltitudeTag` at **165-207**,
> `fillTagFields` (the `hex` fallback) at **209-217**, and the `dst`/`seen_pos`
> sentinels at **397-401**. If you cannot open that repo, treat the chains below as
> frozen and do not "improve" them. `nose` and `trk` differ deliberately — nose is where
> the airframe points, track is where it is going, and they diverge in crosswind.
>
> Two places where the obvious Python is **wrong**:
> - `copyJsonStringTrimmed` truncates **first**, then strips trailing `' '` from the
>   truncated result, and never touches leading whitespace. `v.strip()[:limit]` gives a
>   different answer for `"ABCDEF  X"` (C++ `"ABCDEF"`, naive Python `"ABCDEF  "`).
> - `lroundf` rounds half **away from zero**; Python's `round()` is banker's rounding.
>   `round(3974.5)` is `3974`, `lroundf(3974.5f)` is `3975`.

- [ ] **Step 1: Write the pinned fixture**

The fixture is **pinned, not captured** — the tests assert exact values from this specific
record, so a live capture would fail every time. This record is genuine, taken from the
firmware's own test fixtures (`test/fixtures_adsb.h`).

```bash
cd /Users/matias/Documents/repos/HomeScreen
cat > tests/fixtures/adsb_sample.json <<'EOF'
{"ac":[{"hex":"744828","type":"adsb_icao","flight":"RJA109  ","r":"JY-RAH","t":"A20N","desc":"AIRBUS A-320neo","alt_baro":3675,"alt_geom":3975,"gs":151.0,"ias":149,"tas":162,"track":179.62,"mag_heading":181.41,"true_heading":182.08,"lat":40.621445,"lon":-3.559875,"seen_pos":0.0,"dst":12.328,"dir":30.8}]}
EOF
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_adsb_map.py
import json
import math
from pathlib import Path

import pytest

from homescreen.sources.adsb_map import map_aircraft

FIXTURE = Path(__file__).parent / "fixtures" / "adsb_sample.json"


@pytest.fixture
def real_record() -> dict:
    return json.loads(FIXTURE.read_text())["ac"][0]


def test_maps_a_real_record(real_record):
    out = map_aircraft(real_record)
    assert out is not None
    assert out["lat"] == pytest.approx(40.621445)
    assert out["lon"] == pytest.approx(-3.559875)
    assert out["nose"] == pytest.approx(182.08)
    assert out["trk"] == pytest.approx(179.62)
    assert out["gs"] == pytest.approx(151.0)
    assert out["cs"] == "RJA109", "trailing spaces in `flight` must go"
    assert out["ty"] == "A20N"
    assert out["alt"] == "3675 ft"
    assert out["dst"] == pytest.approx(12.328)
    assert out["age"] == pytest.approx(0.0)


def test_nose_prefers_true_then_mag_then_track_then_dir():
    b = {"lat": 1, "lon": 1}
    assert map_aircraft({**b, "true_heading": 10, "mag_heading": 20,
                         "track": 30, "dir": 40})["nose"] == 10
    assert map_aircraft({**b, "mag_heading": 20, "track": 30, "dir": 40})["nose"] == 20
    assert map_aircraft({**b, "track": 30, "dir": 40})["nose"] == 30
    assert map_aircraft({**b, "dir": 40})["nose"] == 40
    assert map_aircraft(b)["nose"] == 0.0


def test_track_prefers_track_then_true_then_mag_then_dir():
    b = {"lat": 1, "lon": 1}
    assert map_aircraft({**b, "true_heading": 10, "track": 30})["trk"] == 30
    assert map_aircraft({**b, "true_heading": 10, "mag_heading": 20})["trk"] == 10
    assert map_aircraft({**b, "mag_heading": 20, "dir": 40})["trk"] == 20
    assert map_aircraft({**b, "dir": 40})["trk"] == 40


def test_ground_speed_prefers_gs_then_tas_then_ias():
    b = {"lat": 1, "lon": 1}
    assert map_aircraft({**b, "gs": 100, "tas": 200, "ias": 300})["gs"] == 100
    assert map_aircraft({**b, "tas": 200, "ias": 300})["gs"] == 200
    assert map_aircraft({**b, "ias": 300})["gs"] == 300
    assert map_aircraft(b)["gs"] == 0.0


def test_velocity_components_resolve_track_into_east_north():
    kn = 1.852 / 3600.0
    east = map_aircraft({"lat": 1, "lon": 1, "gs": 360, "track": 90})
    assert east["ve"] == pytest.approx(360 * kn, rel=1e-6)
    assert east["vn"] == pytest.approx(0.0, abs=1e-9)
    north = map_aircraft({"lat": 1, "lon": 1, "gs": 360, "track": 0})
    assert north["ve"] == pytest.approx(0.0, abs=1e-9)
    assert north["vn"] == pytest.approx(360 * kn, rel=1e-6)


def test_drops_ground_traffic_by_default():
    assert map_aircraft({"lat": 1, "lon": 1, "alt_baro": "ground"}) is None


def test_keeps_ground_traffic_when_configured_and_tags_it_gnd():
    out = map_aircraft({"lat": 1, "lon": 1, "alt_baro": "ground"}, show_ground=True)
    assert out is not None
    assert out["alt"] == "GND"


def test_drops_records_without_position():
    assert map_aircraft({"gs": 100, "track": 90}) is None
    assert map_aircraft({"lat": 1}) is None


def test_altitude_falls_back_to_geometric_and_rounds_half_away_from_zero():
    assert map_aircraft({"lat": 1, "lon": 1, "alt_geom": 3975.4})["alt"] == "3975 ft"
    # Banker's rounding would give 3974 here; the firmware's lroundf gives 3975.
    assert map_aircraft({"lat": 1, "lon": 1, "alt_geom": 3974.5})["alt"] == "3975 ft"
    assert map_aircraft({"lat": 1, "lon": 1})["alt"] == ""


def test_negative_altitudes_round_away_from_zero():
    # Below sea level is real (Schiphol at -11 ft). lroundf(-75.5) is -76;
    # the negative branch of _round_half_up exists only for this.
    assert map_aircraft({"lat": 1, "lon": 1, "alt_baro": -75.5})["alt"] == "-76 ft"
    assert map_aircraft({"lat": 1, "lon": 1, "alt_baro": -2.5})["alt"] == "-3 ft"


def test_callsign_falls_back_to_hex():
    assert map_aircraft({"lat": 1, "lon": 1, "hex": "744828"})["cs"] == "744828"


def test_text_truncates_before_trimming_like_the_firmware():
    # C++ takes 8 chars "ABCDEF  " then strips trailing spaces -> "ABCDEF".
    out = map_aircraft({"lat": 1, "lon": 1, "flight": "ABCDEF  X"})
    assert out["cs"] == "ABCDEF"
    # Leading whitespace is NOT stripped by the firmware.
    out = map_aircraft({"lat": 1, "lon": 1, "flight": " ABC    "})
    assert out["cs"] == " ABC"


def test_string_fields_respect_firmware_widths():
    out = map_aircraft({"lat": 1, "lon": 1, "flight": "VERYLONGCALLSIGN",
                        "t": "LONGTYPE"})
    assert len(out["cs"]) <= 8
    assert len(out["ty"]) <= 4


def test_missing_dst_is_negative_sentinel():
    assert map_aircraft({"lat": 1, "lon": 1})["dst"] == -1.0


def test_non_finite_numbers_are_rejected():
    # 1e400 is legal RFC-8259 and json.loads makes it inf. Unfiltered it either
    # raises OverflowError out of the fetch loop or serialises as bare
    # `Infinity`, which the firmware's parser rejects wholesale.
    import json as _json
    raw = _json.loads('{"lat":40.5,"lon":-3.6,"alt_baro":1e400,"gs":1e400}')
    out = map_aircraft(raw)          # must not raise
    assert out["alt"] == ""
    assert out["gs"] == 0.0
    assert math.isfinite(out["ve"]) and math.isfinite(out["vn"])
    nan = _json.loads('{"lat":40.5,"lon":-3.6,"alt_baro":NaN}')
    assert map_aircraft(nan)["alt"] == ""


def test_non_finite_position_drops_the_record():
    import json as _json
    assert map_aircraft(_json.loads('{"lat":1e400,"lon":-3.6}')) is None


def test_booleans_are_not_treated_as_numbers():
    # ArduinoJson's is<int>() rejects bool; Python's isinstance(True, int) is True.
    assert map_aircraft({"lat": 1, "lon": 1, "gs": True})["gs"] == 0.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_adsb_map.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homescreen.sources.adsb_map'`

- [ ] **Step 4: Write minimal implementation**

```python
# homescreen/sources/adsb_map.py
"""Map one raw adsb.fi record to the compact form the radar consumes.

Pure: no I/O, no clock. The fallback chains mirror the firmware -- see the
authority note in the plan before changing anything here.
"""

from __future__ import annotations

import math

KNOTS_TO_KM_PER_S = 1.852 / 3600.0

# Widths fixed by the firmware struct (callsign[9], type[5], alt[12]), less NUL.
CS_MAX = 8
TY_MAX = 4
ALT_MAX = 11


def _num(raw: dict, key: str) -> float | None:
    """Numeric read that rejects bool (matching ArduinoJson's is<int>()) and
    non-finite values.

    `1e400` is legal RFC-8259 and `json.loads` turns it into `inf`. Unfiltered
    that does two separate kinds of damage: `_round_half_up(inf)` raises
    OverflowError out of the fetch loop, and any field that skips rounding
    serialises as bare `Infinity`, which is not strict JSON. ArduinoJson is
    built with ARDUINOJSON_ENABLE_INFINITY/NAN = 0, so one poisoned record
    would make `deserializeJson` reject the ENTIRE body and blank the radar for
    every device on the LAN. inf/nan is not a value; it is a broken feed.
    """
    v = raw.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _first_num(raw: dict, keys: tuple[str, ...]) -> float:
    for k in keys:
        v = _num(raw, k)
        if v is not None:
            return v
    return 0.0


def _text(raw: dict, key: str, limit: int) -> str:
    """Truncate THEN strip trailing spaces, matching copyJsonStringTrimmed.
    The reverse order, or stripping both ends, gives different answers."""
    v = raw.get(key)
    if not isinstance(v, str):
        return ""
    return v[:limit].rstrip(" ")


def _round_half_up(x: float) -> int:
    """lroundf semantics: half away from zero, not Python's banker's rounding."""
    return int(math.floor(x + 0.5)) if x >= 0 else int(math.ceil(x - 0.5))


def _altitude_tag(raw: dict) -> str:
    if raw.get("alt_baro") == "ground":
        return "GND"
    alt = _num(raw, "alt_baro")
    if alt is None:
        alt = _num(raw, "alt_geom")
    if alt is None:
        return ""
    return f"{_round_half_up(alt)} ft"[:ALT_MAX]


def map_aircraft(raw: dict, *, show_ground: bool = False) -> dict | None:
    """Return the compact record, or None if this aircraft should be dropped."""
    if raw.get("alt_baro") == "ground" and not show_ground:
        return None

    lat = _num(raw, "lat")
    lon = _num(raw, "lon")
    if lat is None or lon is None:
        return None

    trk = _first_num(raw, ("track", "true_heading", "mag_heading", "dir"))
    gs = _first_num(raw, ("gs", "tas", "ias"))

    # Resolve the track into east/north once here, so the device dead-reckons
    # without trig per frame.
    gs_km_s = gs * KNOTS_TO_KM_PER_S
    trk_rad = math.radians(trk)

    dst = _num(raw, "dst")
    age = _num(raw, "seen_pos")

    return {
        "lat": lat,
        "lon": lon,
        "nose": _first_num(raw, ("true_heading", "mag_heading", "track", "dir")),
        "trk": trk,
        "gs": gs,
        "ve": gs_km_s * math.sin(trk_rad),
        "vn": gs_km_s * math.cos(trk_rad),
        "age": age if age is not None else 0.0,
        "dst": dst if dst is not None else -1.0,
        "cs": _text(raw, "flight", CS_MAX) or _text(raw, "hex", CS_MAX),
        "ty": _text(raw, "t", TY_MAX),
        "alt": _altitude_tag(raw),
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_adsb_map.py -v`
Expected: PASS — **17 tests**

- [ ] **Step 6: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/sources/adsb_map.py tests/test_adsb_map.py tests/fixtures/adsb_sample.json
git commit -m "Add pure adsb.fi record mapping matching firmware fallback chains"
```

---

## Task 3: Config and fetcher

**Runs on: Mac**

**Files:**
- Create: `homescreen/config.py`, `homescreen/sources/adsb.py`
- Test: `tests/test_adsb.py`

**Interfaces:**
- Consumes: `map_aircraft` (Task 2), `write_cache`/`write_failure` (Task 1)
- Produces:
  - `load_config(path: Path) -> dict` — raises `ValueError` on an empty or non-mapping file
  - `device(cfg: dict, device_id: str) -> dict | None`
  - `feed_cache_path(cache_dir: Path, dev: dict) -> Path` — `cache/feed/{dev['feed']}.json`
  - `fetch_radar(cfg: dict, cache_path: Path, *, session=None) -> bool` — never raises
  - `run_forever(cfg: dict, cache_path: Path) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adsb.py
import copy
import json
from pathlib import Path

import pytest

from homescreen.cache import read_cache, write_cache
from homescreen.config import device, feed_cache_path, load_config
from homescreen.sources.adsb import fetch_radar

CFG = {
    "feeds": {"adsb": {"endpoint": "https://example.invalid/api", "fetch_seconds": 3}},
    "devices": [{"id": "radar", "kind": "gc9a01_client", "render": "device",
                 "feed": "adsb", "home": {"lat": 40.4168, "lon": -3.7038},
                 "radius_km": 60, "max_aircraft": 20, "show_ground": False}],
}
DEV = CFG["devices"][0]


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls, self.last_url = response, exc, 0, None

    def get(self, url, timeout=None):
        self.calls += 1
        self.last_url = url
        if self.exc:
            raise self.exc
        return self.response


def _ac(**kw):
    base = {"lat": 40.5, "lon": -3.6, "gs": 400, "track": 90, "seen_pos": 1.0}
    base.update(kw)
    return base


def test_writes_mapped_aircraft_to_cache(tmp_path: Path):
    s = FakeSession(FakeResponse({"ac": [_ac(flight="IBE1 ", dst=5.0)]}))
    assert fetch_radar(CFG, DEV, tmp_path / "radar.json", session=s) is True
    env = read_cache(tmp_path / "radar.json")
    assert env["ok"] is True
    assert [a["cs"] for a in env["data"]["aircraft"]] == ["IBE1"]


def test_url_converts_radius_km_to_nautical_miles(tmp_path: Path):
    s = FakeSession(FakeResponse({"ac": []}))
    fetch_radar(CFG, DEV, tmp_path / "radar.json", session=s)
    # 60 km / 1.852 = 32.4 NM
    assert "/dist/32.4" in s.last_url
    assert "/lat/40.416800/lon/-3.703800" in s.last_url


def test_drops_ground_traffic(tmp_path: Path):
    s = FakeSession(FakeResponse({"ac": [_ac(alt_baro="ground"), _ac(dst=1.0)]}))
    fetch_radar(CFG, DEV, tmp_path / "radar.json", session=s)
    assert len(read_cache(tmp_path / "radar.json")["data"]["aircraft"]) == 1


def test_filters_out_aircraft_beyond_the_configured_radius(tmp_path: Path):
    # radius_km 60 == 32.4 NM. dst is in NAUTICAL MILES -- getting this
    # conversion backwards is the easy bug.
    s = FakeSession(FakeResponse({"ac": [
        _ac(dst=10.0, flight="IN"),
        _ac(dst=40.0, flight="OUT"),   # 74 km, outside the 60 km ring
    ]}))
    fetch_radar(CFG, DEV, tmp_path / "radar.json", session=s)
    kept = [a["cs"] for a in read_cache(tmp_path / "radar.json")["data"]["aircraft"]]
    assert kept == ["IN"]


def test_caps_at_max_aircraft_keeping_nearest(tmp_path: Path):
    cfg = copy.deepcopy(CFG)
    cfg["devices"][0]["max_aircraft"] = 2
    s = FakeSession(FakeResponse({"ac": [
        _ac(dst=20.0, flight="FAR"),
        _ac(dst=1.0, flight="NEAR"),
        _ac(dst=10.0, flight="MID"),
    ]}))
    fetch_radar(cfg, cfg["devices"][0], tmp_path / "radar.json", session=s)
    kept = [a["cs"] for a in read_cache(tmp_path / "radar.json")["data"]["aircraft"]]
    assert kept == ["NEAR", "MID"], "nearest first, then capped"


def test_network_failure_preserves_previous_data(tmp_path: Path):
    p = tmp_path / "radar.json"
    write_cache(p, {"aircraft": [{"cs": "OLD"}]})
    s = FakeSession(exc=OSError("no route to host"))
    assert fetch_radar(CFG, DEV, p, session=s) is False
    env = read_cache(p)
    assert env["ok"] is False
    assert "no route to host" in env["error"]
    assert env["data"]["aircraft"][0]["cs"] == "OLD"


@pytest.mark.parametrize("payload", [{"ac": 5}, {}, {"ac": None}, [], "nope",
                                     "<html>captive portal</html>"])
def test_non_array_ac_is_a_failure_and_keeps_the_last_good_list(tmp_path: Path, payload):
    # An empty sky is `[]` and nothing else. The firmware refuses these
    # explicitly (adsb_client.cpp:355-366) precisely because treating them as
    # "no traffic" wipes real aircraft off the panel -- and the Pi handing the
    # device a well-formed empty list would sail past the device's own guard.
    p = tmp_path / "radar.json"
    write_cache(p, {"aircraft": [{"cs": "REAL", "dst": 1.0}]})
    s = FakeSession(FakeResponse(payload))
    assert fetch_radar(CFG, DEV, p, session=s) is False
    env = read_cache(p)
    assert env["ok"] is False
    assert env["data"]["aircraft"][0]["cs"] == "REAL", "real traffic must survive"


def test_literal_empty_array_is_an_empty_sky_and_succeeds(tmp_path: Path):
    p = tmp_path / "radar.json"
    s = FakeSession(FakeResponse({"ac": []}))
    assert fetch_radar(CFG, DEV, p, session=s) is True
    assert read_cache(p)["data"]["aircraft"] == []


def test_non_dict_entries_are_skipped_not_fatal(tmp_path: Path):
    s = FakeSession(FakeResponse({"ac": [None, 7, _ac(dst=2.0, flight="OK")]}))
    p = tmp_path / "radar.json"
    assert fetch_radar(CFG, DEV, p, session=s) is True
    assert [a["cs"] for a in read_cache(p)["data"]["aircraft"]] == ["OK"]


def test_keeps_records_with_no_distance(tmp_path: Path):
    # The firmware keeps these (dst_nm = -1) and projects from lat/lon.
    s = FakeSession(FakeResponse({"ac": [_ac(flight="NODST")]}))
    p = tmp_path / "radar.json"
    fetch_radar(CFG, DEV, p, session=s)
    assert [a["cs"] for a in read_cache(p)["data"]["aircraft"]] == ["NODST"]


def test_show_ground_config_is_honoured(tmp_path: Path):
    cfg = copy.deepcopy(CFG)
    cfg["devices"][0]["show_ground"] = True
    s = FakeSession(FakeResponse({"ac": [_ac(alt_baro="ground", dst=1.0, flight="TUG")]}))
    p = tmp_path / "radar.json"
    fetch_radar(cfg, cfg["devices"][0], p, session=s)
    kept = read_cache(p)["data"]["aircraft"]
    assert [a["cs"] for a in kept] == ["TUG"]
    assert kept[0]["alt"] == "GND"


def test_repeated_identical_failures_do_not_rewrite_the_cache(tmp_path: Path):
    # An fsync per cycle during an outage is ~28,800 SD writes/day -- the same
    # wear this plan rejected a systemd timer to avoid.
    p = tmp_path / "radar.json"
    write_cache(p, {"aircraft": [{"cs": "OLD", "dst": 1.0}]})
    s = FakeSession(exc=OSError("no route to host"))
    fetch_radar(CFG, DEV, p, session=s)
    first = p.stat().st_mtime_ns
    fetch_radar(CFG, DEV, p, session=s)
    assert p.stat().st_mtime_ns == first, "identical failure must not rewrite"
    # A DIFFERENT error must still be recorded.
    fetch_radar(CFG, DEV, p, session=FakeSession(exc=OSError("dns failure")))
    assert "dns failure" in read_cache(p)["error"]


def test_local_config_overlays_secrets(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "feeds:\n  adsb:\n    endpoint: public\n    fetch_seconds: 3\n")
    (tmp_path / "config.local.yaml").write_text("feeds:\n  adsb:\n    api_key: s3cret\n")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg["feeds"]["adsb"]["api_key"] == "s3cret", "local overlay must merge in"
    assert cfg["feeds"]["adsb"]["endpoint"] == "public", "and not clobber siblings"
    assert cfg["feeds"]["adsb"]["fetch_seconds"] == 3


def test_non_finite_payload_is_a_failure_not_a_crash(tmp_path: Path):
    # The daemon must not die: an escaping exception exits run_forever, and
    # Restart=always then refetches the same poisoned record ~8,640 times a day.
    import json as _json
    p = tmp_path / "radar.json"
    write_cache(p, {"aircraft": [{"cs": "REAL", "dst": 1.0}]})
    payload = _json.loads('{"ac":[{"lat":40.5,"lon":-3.6,"alt_baro":1e400,"dst":1.0}]}')
    s = FakeSession(FakeResponse(payload))
    assert fetch_radar(CFG, DEV, p, session=s) is True, "a finite-able record still maps"
    # And nothing non-finite can be persisted even if a future field skips _num.
    env = read_cache(p)
    assert "Infinity" not in json.dumps(env)


def test_http_error_is_a_failure_not_a_crash(tmp_path: Path):
    s = FakeSession(FakeResponse({}, status=503))
    assert fetch_radar(CFG, DEV, tmp_path / "radar.json", session=s) is False


def test_load_config_rejects_empty_file(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("")
    with pytest.raises(ValueError):
        load_config(p)


def test_device_lookup_and_feed_path(tmp_path: Path):
    dev = device(CFG, "radar")
    assert dev["id"] == "radar"
    assert device(CFG, "nope") is None
    assert feed_cache_path(tmp_path, dev) == tmp_path / "feed" / "radar.json"
    # Per DEVICE, not a literal and not per feed: two radars with different
    # centres must not collide on one file.
    assert (feed_cache_path(tmp_path, {"id": "radar2", "feed": "adsb"})
            == tmp_path / "feed" / "radar2.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_adsb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homescreen.config'`

- [ ] **Step 3: Write `homescreen/config.py`**

```python
# homescreen/config.py
from __future__ import annotations

from pathlib import Path

import yaml


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path) -> dict:
    """Load config.yaml, then overlay config.local.yaml if present.

    config.yaml is COMMITTED (it is structure: device registry, coordinates,
    cadence). config.local.yaml is gitignored and holds secrets -- SPEC §6 puts
    `quotes.api_key` and the secret `calendar.ics_url` in the config, and this
    repo is public (CLAUDE.md §3/§9). Without this overlay the first person to
    add a key has exactly one obvious place to put it: the committed file.
    """
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} is empty or is not a mapping")
    # NOTE: lists are replaced wholesale, not merged by key. A `devices:` list
    # in the local file therefore REPLACES the registry rather than adding to
    # it. Fine for secrets (scalars under `feeds`); do not put devices there.
    local = path.with_name("config.local.yaml")
    if local.exists():
        with open(local, encoding="utf-8") as fh:
            overlay = yaml.safe_load(fh)
        if isinstance(overlay, dict):
            cfg = _deep_merge(cfg, overlay)
    return cfg


def device(cfg: dict, device_id: str) -> dict | None:
    for d in cfg.get("devices", []):
        if isinstance(d, dict) and d.get("id") == device_id:
            return d
    return None


def feed_cache_path(cache_dir: Path, dev: dict) -> Path:
    """Cache file for this device. Keyed by device id, not by a literal and not
    by feed: `home`, `radius_km` and `max_aircraft` are per-device, so two
    radars with different centres must not share one file."""
    return cache_dir / "feed" / f"{dev['id']}.json"
```

- [ ] **Step 4: Write `homescreen/sources/adsb.py`**

```python
# homescreen/sources/adsb.py
"""Fetch ADS-B traffic and cache it.

Runs as its own daemon on a cadence read from config, never on a device
request (VALIDATION C7). A looping daemon rather than a systemd timer so the
interval lives in config (VALIDATION C6), so ~29k interpreter spawns/day of
journal churn do not land on an SD card (CLAUDE.md §2), and so a slow upstream
cannot overlap requests and breach adsb.fi's 1 req/s limit.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from homescreen.cache import write_cache, write_failure
from homescreen.config import device, feed_cache_path, load_config
from homescreen.sources.adsb_map import map_aircraft

log = logging.getLogger(__name__)

KM_PER_NM = 1.852
# (connect, read). INVARIANT: fetch_seconds + read_timeout < STALE_HORIZON_S
# (12.0). run_forever sleeps AFTER the request, so worst-case dwell is
# fetch_duration + fetch_seconds; a scalar timeout=10 would put that at 13 s,
# past the device's hard kExtrapolationHorizonSec, dimming every target once
# per cycle on a slow-but-successful fetch.
REQUEST_TIMEOUT_S = (3.05, 5)


def _url(endpoint: str, lat: float, lon: float, radius_km: float) -> str:
    dist_nm = radius_km / KM_PER_NM
    return (f"{endpoint.rstrip('/')}/lat/{lat:.6f}"
            f"/lon/{lon:.6f}/dist/{dist_nm:.1f}")


def fetch_radar(cfg: dict, dev: dict, cache_path: Path, *, session=None) -> bool:
    """Fetch, map and cache for ONE device. Returns True. Never raises.

    `dev` is passed in rather than looked up by the literal "radar": the cache
    is per-device, so the parameters must be too.
    """
    if session is None:
        import requests
        session = requests.Session()

    home = dev.get("home", {})
    radius_km = float(dev.get("radius_km", 60))
    endpoint = cfg.get("feeds", {}).get("adsb", {}).get("endpoint", "")

    try:
        url = _url(endpoint, float(home.get("lat", 0.0)),
                   float(home.get("lon", 0.0)), radius_km)
        resp = session.get(url, timeout=REQUEST_TIMEOUT_S)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - nothing may escape into the cache
        log.warning("adsb fetch failed: %s", exc)
        write_failure(cache_path, str(exc))
        return False

    raw_list = payload.get("ac") if isinstance(payload, dict) else None
    if not isinstance(raw_list, list):
        # An empty sky is `[]` and nothing else. A missing/null/scalar `ac`, or
        # an HTML captive-portal page, is not this API -- and treating it as "no
        # traffic" wipes real aircraft off the panel. The firmware refuses it
        # explicitly (adsb_client.cpp:355-366, "the last good list is kept until
        # it expires"); moving the fetch here REMOVES that guard, because we
        # would hand the device a well-formed empty list its own check passes.
        log.warning("adsb: response is not the aircraft feed")
        write_failure(cache_path, "response is not the aircraft feed")
        return False

    radius_nm = radius_km / KM_PER_NM
    show_ground = bool(dev.get("show_ground", False))
    aircraft = []
    try:
        aircraft = _map_all(raw_list, radius_nm, show_ground)
        # write_cache is INSIDE the try on purpose: it refuses to serialise a
        # non-finite, and that refusal must become a recorded failure rather
        # than an exception escaping into run_forever's loop.
        write_cache(cache_path,
                    {"aircraft": aircraft[:int(dev.get("max_aircraft", 20))]})
    except Exception as exc:  # noqa: BLE001 - "never raises" must be structural
        log.warning("adsb mapping failed: %s", exc)
        write_failure(cache_path, f"mapping failed: {exc}")
        return False
    return True


def _map_all(raw_list: list, radius_nm: float, show_ground: bool) -> list:
    aircraft = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        mapped = map_aircraft(raw, show_ground=show_ground)
        if mapped is None:
            continue
        # Upstream bounds by a rounded `dist`; enforce our own radius too.
        # A record with lat/lon but no `dst` is still drawable -- the firmware
        # keeps it (`dst_nm = ... : -1.0f`) and projects from lat/lon -- so only
        # drop records we can positively place outside the ring.
        if mapped["dst"] >= 0 and mapped["dst"] > radius_nm:
            continue
        aircraft.append(mapped)

    # Nearest first so the cap keeps what matters; dst-less records sort last.
    aircraft.sort(key=lambda a: a["dst"] if a["dst"] >= 0 else float("inf"))
    return aircraft


def run_forever(cfg: dict, dev: dict, cache_path: Path) -> None:
    interval = float(cfg.get("feeds", {}).get("adsb", {}).get("fetch_seconds", 3))
    import requests
    session = requests.Session()
    log.info("adsb fetch loop starting for %s, interval %.1fs", dev["id"], interval)
    while True:
        fetch_radar(cfg, dev, cache_path, session=session)
        # Sleep AFTER the request completes, so a slow upstream stretches the
        # cycle instead of queueing a second in-flight request.
        time.sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    root = Path(__file__).resolve().parents[2]
    cfg = load_config(root / "config.yaml")
    dev = device(cfg, "radar")
    if dev is None:
        raise SystemExit("config.yaml has no device with id 'radar'")
    run_forever(cfg, dev, feed_cache_path(root / "cache", dev))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_adsb.py -v`
Expected: PASS — **22 tests** (16 functions, one parametrized 6 ways)

- [ ] **Step 6: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/config.py homescreen/sources/adsb.py tests/test_adsb.py
git commit -m "Add ADS-B fetch loop with radius filter and non-raising failure path"
```

---

## Task 4: Serve endpoints

**Runs on: Mac**

**Files:**
- Create: `homescreen/serve.py`
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: `read_cache` (Task 1), `device`/`feed_cache_path`/`load_config` (Task 3)
- Produces: `create_app(cfg: dict, cache_dir: Path, *, clock=time.time) -> Flask`, serving `GET /api/display/<device_id>/data` and `GET /api/display/<device_id>/health`

> **This is the task VALIDATION F4 exists for.** The device dead-reckons from
> `age + seconds-since-its-own-fetch` against a **12 second** horizon. If the server
> returns the age recorded at *fetch* time, the cache dwell is invisible and every
> aircraft draws behind its true position.
>
> The test below deliberately does **not** stub `_cache_epoch`. Stubbing it would leave
> only `age + dwell` under test — arithmetic that was never in doubt — while the part
> that can actually break, parsing `fetched_at` back into an epoch, would never run. If
> `_now_iso()` ever lost its timezone, `fromisoformat().timestamp()` would reinterpret it
> as local time: a **7200 s** error in Madrid CEST, against a 12 s horizon, with a green
> suite. So the test stamps the envelope through the real `_now_iso` and lets the real
> parse run.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_serve.py
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from homescreen.cache import write_cache, write_failure
from homescreen.serve import create_app

CFG = {
    "devices": [{"id": "radar", "kind": "gc9a01_client", "render": "device",
                 "feed": "adsb", "poll_seconds": 7,
                 "home": {"lat": 40.4168, "lon": -3.7038},
                 "radius_km": 60, "max_aircraft": 20}],
}


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    clock = Clock()
    # Stamp cache writes from the fake clock, so the REAL _now_iso ->
    # fromisoformat -> timestamp path is exercised end to end.
    # Stamp at +00:00, NOT .astimezone(). With a local stamp, dropping the
    # offset makes fromisoformat() reinterpret local-as-local and the two errors
    # cancel exactly -- the tz bug would be undetectable in every timezone.
    monkeypatch.setattr(
        "homescreen.cache._now_iso",
        lambda: datetime.fromtimestamp(clock.t, timezone.utc).isoformat())
    app = create_app(CFG, tmp_path, clock=clock)
    return app.test_client(), tmp_path / "feed" / "radar.json", clock


def _seed(path, age=2.0):
    write_cache(path, {"aircraft": [
        {"lat": 40.5, "lon": -3.6, "nose": 90.0, "trk": 90.0, "gs": 400.0,
         "ve": 0.2, "vn": 0.0, "age": age, "dst": 7.4,
         "cs": "IBE1", "ty": "A320", "alt": "3675 ft"}]})


def test_age_is_recomputed_at_serve_time(ctx):
    client, path, clock = ctx
    _seed(path, age=2.0)
    clock.t += 4.0
    body = client.get("/api/display/radar/data").get_json()
    assert body["aircraft"][0]["age"] == pytest.approx(6.0, abs=0.01), (
        "age must be fix-age + cache dwell (VALIDATION F4)")


def test_feed_age_reports_cache_dwell(ctx):
    client, path, clock = ctx
    _seed(path)
    clock.t += 3.5
    body = client.get("/api/display/radar/data").get_json()
    assert body["feed"]["age_s"] == pytest.approx(3.5, abs=0.01)
    assert body["feed"]["ok"] is True


def test_feed_age_keeps_growing_while_fetches_fail(ctx):
    client, path, clock = ctx
    _seed(path)
    clock.t += 10.0
    write_failure(path, "boom")
    clock.t += 5.0
    body = client.get("/api/display/radar/data").get_json()
    assert body["feed"]["ok"] is False
    assert body["feed"]["age_s"] == pytest.approx(15.0, abs=0.01), (
        "age tracks last SUCCESS, so a dead feed is visible (SPEC 11.4)")
    assert body["aircraft"], "stale data is still served (SPEC 11.3)"


def test_missing_cache_serves_empty_not_500(ctx):
    client, _, _ = ctx
    resp = client.get("/api/display/radar/data")
    assert resp.status_code == 200
    assert resp.get_json()["aircraft"] == []
    assert resp.get_json()["feed"]["ok"] is False


JUNK = [
    "{not json",
    '{"data": {}}',
    '{"fetched_at": "garbage", "ok": true, "data": {"aircraft": []}}',
    '{"fetched_at": "2026-01-01T00:00:00+00:00", "ok": true, "data": {"aircraft": [{}]}}',
    # Non-iterable / wrong-type aircraft: these are the shapes that actually
    # raise. read_cache guarantees `data` is a dict, not what is inside it.
    '{"fetched_at": "2026-01-01T00:00:00+00:00", "ok": true, "data": {"aircraft": 5}}',
    '{"fetched_at": "2026-01-01T00:00:00+00:00", "ok": true, "data": {"aircraft": null}}',
    '{"fetched_at": "2026-01-01T00:00:00+00:00", "ok": true, "data": {"aircraft": "x"}}',
]


@pytest.mark.parametrize("junk", JUNK)
def test_malformed_cache_never_500s(ctx, junk):
    client, path, _ = ctx
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(junk)
    resp = client.get("/api/display/radar/data")
    assert resp.status_code == 200, f"/data must degrade, not raise, on {junk!r}"


@pytest.mark.parametrize("junk", JUNK)
def test_malformed_cache_never_500s_on_health(ctx, junk):
    client, path, _ = ctx
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(junk)
    resp = client.get("/api/display/radar/health")
    assert resp.status_code == 200, f"/health must degrade, not raise, on {junk!r}"


def test_health_count_matches_what_data_serves(ctx):
    client, path, _ = ctx
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"fetched_at": "2026-01-01T00:00:00+00:00", "ok": true,'
                    ' "data": {"aircraft": [{"age": "zz"}, [1, 2]]}}')
    served = len(client.get("/api/display/radar/data").get_json()["aircraft"])
    reported = client.get("/api/display/radar/health").get_json()["feed"]["aircraft"]
    assert served == reported == 0, "/health must not count records /data drops"


def test_cache_epoch_honours_the_utc_offset():
    # Pins both ends regardless of the machine's TZ. Without this, stripping
    # tzinfo anywhere in the chain is invisible to the suite.
    from homescreen.serve import _cache_epoch
    assert _cache_epoch({"fetched_at": "2026-01-01T00:00:00+00:00"}) == 1767225600.0
    assert _cache_epoch({"fetched_at": "2026-01-01T02:00:00+02:00"}) == 1767225600.0


def test_future_stamp_is_reported_as_not_ok_not_as_fresh(ctx):
    # A Pi 4 has no RTC; pre-NTP stamps sit in the future. Clamping to 0.0 would
    # claim a perfectly fresh feed over arbitrarily old data.
    client, path, clock = ctx
    _seed(path)
    clock.t -= 3600.0
    body = client.get("/api/display/radar/data").get_json()
    assert body["feed"]["ok"] is False, "clock skew must not read as freshness"


def test_poll_seconds_header_comes_from_config(ctx):
    client, path, _ = ctx
    _seed(path)
    # 7, deliberately NOT the code's fallback value, so the test would fail if
    # the config were ignored.
    assert client.get("/api/display/radar/data").headers["X-Poll-Seconds"] == "7"


def test_etag_survives_a_refetch_of_identical_data(ctx):
    # THE production property. The daemon rewrites the cache every
    # fetch_seconds with a fresh fetched_at; if the ETag tracked that stamp,
    # a device would never once receive a 304.
    client, path, clock = ctx
    _seed(path, age=2.0)
    etag = client.get("/api/display/radar/data").headers["ETag"]
    clock.t += 3.0
    _seed(path, age=2.0)          # same sky, new fetched_at
    again = client.get("/api/display/radar/data", headers={"If-None-Match": etag})
    assert again.status_code == 304, "identical content must still 304 after a refetch"
    assert again.get_data() == b""


def test_stale_feed_is_never_304ed(ctx):
    # A 304 has no body, so feed.age_s -- the device's only stall signal --
    # could not reach it. Past the horizon we must always send a body.
    client, path, clock = ctx
    _seed(path)
    etag = client.get("/api/display/radar/data").headers["ETag"]
    clock.t += 30.0               # well past STALE_HORIZON_S
    again = client.get("/api/display/radar/data", headers={"If-None-Match": etag})
    assert again.status_code == 200, "a rotting feed must not be answered with 304"
    assert again.get_json()["feed"]["age_s"] == pytest.approx(30.0, abs=0.01)


def test_feed_liveness_headers_are_set_on_both_branches(ctx):
    client, path, clock = ctx
    _seed(path)
    r200 = client.get("/api/display/radar/data")
    assert r200.headers["X-Feed-Ok"] == "1"
    clock.t += 2.0
    _seed(path)
    r304 = client.get("/api/display/radar/data",
                      headers={"If-None-Match": r200.headers["ETag"]})
    assert r304.status_code == 304
    assert r304.headers["X-Feed-Ok"] == "1"
    assert float(r304.headers["X-Feed-Age"]) >= 0.0, "liveness must survive a 304"


def test_etag_changes_when_the_cache_changes(ctx):
    client, path, clock = ctx
    _seed(path, age=2.0)
    a = client.get("/api/display/radar/data").headers["ETag"]
    clock.t += 1.0
    _seed(path, age=9.0)
    b = client.get("/api/display/radar/data").headers["ETag"]
    assert a != b


def test_unknown_device_is_404(ctx):
    client, _, _ = ctx
    assert client.get("/api/display/nope/data").status_code == 404


def test_routing_is_on_render_not_kind(tmp_path):
    # ADDENDUM §6: "Each entry declares its render mode; serve.py routes on it."
    # Both cases deliberately make `render` and `kind` DISAGREE -- with agreeing
    # values, routing on `kind` would pass this test too and prove nothing.
    server_rendered = {"devices": [{"id": "odd", "kind": "gc9a01_client",
                                    "render": "server", "feed": "adsb"}]}
    c = create_app(server_rendered, tmp_path).test_client()
    assert c.get("/api/display/odd/data").status_code == 404, (
        "render: server must not be served from the data endpoint, "
        "even for a gc9a01_client")

    novel_kind = {"devices": [{"id": "newthing", "kind": "some_future_client",
                               "render": "device", "feed": "adsb",
                               "poll_seconds": 9}]}
    c = create_app(novel_kind, tmp_path).test_client()
    r = c.get("/api/display/newthing/data")
    assert r.status_code == 200, (
        "a new data-push class must work by declaring render: device, "
        "without editing a Python set")
    assert r.headers["X-Poll-Seconds"] == "9"


def test_health_reports_feed_state_and_last_telemetry(ctx):
    client, path, clock = ctx
    _seed(path)
    client.get("/api/display/radar/data?rssi=-64&uptime=884213&fw=1.2.0&errors=0")
    clock.t += 2.0
    health = client.get("/api/display/radar/health").get_json()
    assert health["feed"]["ok"] is True
    assert health["feed"]["age_s"] == pytest.approx(2.0, abs=0.01)
    assert health["last_telemetry"]["rssi"] == "-64"
    assert health["last_telemetry"]["fw"] == "1.2.0"


def test_serve_module_imports_no_http_client():
    # VALIDATION C7 enforced structurally. Patching a symbol serve.py never
    # imports would be a test that cannot fail. Checked in a SUBPROCESS because
    # sys.modules is polluted by every other test module in the session --
    # in-process this would assert on test ordering, not on serve.py.
    import subprocess
    import sys
    # http.client and socket are excluded deliberately: Flask/Werkzeug import
    # both at baseline (verified: `import flask` alone pulls them), so they say
    # nothing about our code. An outbound client would show as `requests` or
    # `urllib.request`.
    code = (
        "import homescreen.serve, sys; "
        "bad = [m for m in ('requests', 'urllib.request', "
        "'homescreen.sources.adsb') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True, cwd=root,
                         env={**os.environ, "PYTHONPATH": str(root)}).stdout.strip()
    assert out == "", f"serve path must perform no network I/O; pulled in: {out}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_serve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'homescreen.serve'`

- [ ] **Step 3: Write minimal implementation**

```python
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
from homescreen.config import device, feed_cache_path

log = logging.getLogger(__name__)

# ADDENDUM §6: "Each entry declares its render mode; serve.py routes on it."
# Routing on `render` rather than `kind` keeps that field live config -- a new
# data-push device class then declares itself instead of needing a Python edit.
DATA_RENDER = "device"

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
    # A non-finite that somehow reached the cache must not reach the wire.
    app.json.allow_nan = False
    telemetry: dict[str, dict] = {}

    def _lookup(device_id: str):
        dev = device(cfg, device_id)
        if dev is None or dev.get("render") != DATA_RENDER:
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
                     "source": cfg.get("feeds", {}).get("adsb", {}).get("source", "api")},
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
        resp.headers["X-Poll-Seconds"] = str(dev.get("poll_seconds", 5))
        # Set on BOTH branches: headers survive a 304, so a device that does get
        # one still learns the feed's liveness without a body.
        resp.headers["X-Feed-Age"] = f"{dwell:.1f}"
        resp.headers["X-Feed-Ok"] = "1" if ok else "0"
        return resp

    @app.get("/api/display/<device_id>/health")
    def health(device_id: str):
        dev = _lookup(device_id)
        if dev is None:
            return jsonify({"error": "unknown device"}), 404
        now = clock()
        env = read_cache(feed_cache_path(cache_dir, dev))
        ok, dwell = _feed_state(env, now)
        return jsonify({
            "device": device_id,
            "server_time": int(now),
            "feed": {"ok": ok, "age_s": dwell,
                     "error": (env or {}).get("error"),
                     "fetched_at": (env or {}).get("fetched_at"),
                     "aircraft": len(_servable(env, dwell))},
            "last_telemetry": telemetry.get(device_id),
        })

    return app


def main() -> None:
    from homescreen.config import load_config
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Werkzeug logs one access line per request: at poll_seconds=5 that is
    # ~17k journal lines/day per device onto a microSD whose wear CLAUDE.md §2
    # flags as unmitigated. Task 6 also caps journald.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "config.yaml")
    srv = cfg.get("server", {})
    # Werkzeug's dev server. Adequate for a handful of LAN devices; swap for
    # waitress in Phase C when N clients start pulling 48 KB frames.
    create_app(cfg, root / "cache").run(
        host=srv.get("host", "0.0.0.0"), port=int(srv.get("port", 8080)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_serve.py -v`
Expected: PASS — **30 tests** (18 functions, two parametrized 7 ways each)

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/pytest -v`
Expected: PASS — **83 tests** (14 + 17 + 22 + 30)

- [ ] **Step 6: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add homescreen/serve.py tests/test_serve.py
git commit -m "Add radar data and health endpoints with serve-time age recomputation"
```

---

## Task 5: Local end-to-end and systemd units

**Runs on: Mac**

**Files:**
- Create: `systemd/homescreen-serve.service`, `systemd/homescreen-fetch.service`

**Interfaces:**
- Consumes: everything above
- Produces: two installable system units targeting `/home/pi/dashboard`

- [ ] **Step 1: Verify end-to-end against the real upstream, locally**

`timeout` is GNU coreutils and macOS ships neither it nor `gtimeout`. `fetch_radar` is
importable and does exactly one request, so call it directly instead of bounding the loop.

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/python - <<'EOF'
from pathlib import Path
from homescreen.config import load_config, device, feed_cache_path
from homescreen.sources.adsb import fetch_radar
cfg = load_config(Path("config.yaml"))
dev = device(cfg, "radar")
path = feed_cache_path(Path("cache"), dev)
print("fetch ok:", fetch_radar(cfg, dev, path))
import json
e = json.load(open(path))
# .get(), not [...]: on a first-ever failure `data` is {} and this diagnostic
# must report the error, not die with a KeyError on top of it.
ac = e["data"].get("aircraft", [])
print("ok:", e["ok"], "| error:", e["error"], "| aircraft:", len(ac))
if ac:
    print("nearest:", {k: ac[0][k] for k in ("cs", "ty", "alt", "dst", "age")})
EOF
```

Expected: `fetch ok: True` and a non-zero count during daylight over Madrid. Zero aircraft
is valid at night — not a failure, but it makes Step 2's aircraft check less informative.
An HTTP 404 here means the `api/v3` version literal in `config.yaml`, not the code.

- [ ] **Step 2: Verify the served age advances in real time**

```bash
cd /Users/matias/Documents/repos/HomeScreen
venv/bin/python -m homescreen.serve & SERVE_PID=$!
sleep 2
venv/bin/python - <<'EOF'
import json, time, urllib.request
U = "http://127.0.0.1:8080/api/display/radar/data"
a = json.load(urllib.request.urlopen(U))
time.sleep(3)
b = json.load(urllib.request.urlopen(U))
print("feed.age_s:", round(a["feed"]["age_s"], 1), "->", round(b["feed"]["age_s"], 1))
if a["aircraft"] and b["aircraft"]:
    print("aircraft[0].age:", round(a["aircraft"][0]["age"], 1),
          "->", round(b["aircraft"][0]["age"], 1))
else:
    print("no aircraft in view; feed.age_s above is the check")
assert b["feed"]["age_s"] > a["feed"]["age_s"], "dwell must grow with no fetcher running"
print("PASS: serve-time age advances")
EOF
curl -sI http://127.0.0.1:8080/api/display/radar/data | grep -iE "etag|x-poll"
curl -s http://127.0.0.1:8080/api/display/radar/health | venv/bin/python -m json.tool
kill $SERVE_PID
```

Expected: both ages grow by ~3 s, `ETag` and `X-Poll-Seconds: 5` present, health returns
`feed.ok: true`. The `kill` matters — a stray process on :8080 makes the Pi's unit fail
later with `Address already in use`.

- [ ] **Step 3: Write the serve unit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
cat > systemd/homescreen-serve.service <<'EOF'
[Unit]
Description=HomeScreen display backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/dashboard
ExecStart=/home/pi/dashboard/venv/bin/python -m homescreen.serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

- [ ] **Step 4: Write the fetch unit**

A `Type=simple` daemon, not a timer: the cadence belongs in `config.yaml` (VALIDATION C6),
and a `oneshot` firing every 3 s would spawn ~29,000 interpreters and ~86,000 journal lines
a day onto an SD card whose wear risk CLAUDE.md §2 flags as unmitigated.

```bash
cd /Users/matias/Documents/repos/HomeScreen
cat > systemd/homescreen-fetch.service <<'EOF'
[Unit]
Description=HomeScreen ADS-B fetch loop
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/dashboard
ExecStart=/home/pi/dashboard/venv/bin/python -m homescreen.sources.adsb
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

- [ ] **Step 5: Commit**

```bash
cd /Users/matias/Documents/repos/HomeScreen
git add systemd/
git commit -m "Add systemd units for the serve and fetch daemons"
git push
```

---

## Task 6: Deploy to the Pi

**Runs on: Mac.** Every Pi-side command is explicitly prefixed `ssh pi@dashboard.local`;
everything else runs locally. Per-line `# Mac` / `# Pi` comments where a block mixes both.

**Files:** none created — this task installs what Tasks 0–5 produced.

- [ ] **Step 0: Grant passwordless sudo — HUMAN, INTERACTIVE, ONE TIME**

**An agent cannot do this step.** This Pi is Debian trixie, not a NOPASSWD Raspberry Pi OS
image: `ssh pi@dashboard.local 'sudo id'` returns *"sudo: a terminal is required to read the
password"*. Every `sudo` in the steps below fails without this. Run it yourself, at a
terminal, and type the password when prompted:

```bash
ssh -t pi@dashboard.local 'echo "pi ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/010_pi-nopasswd && sudo chmod 440 /etc/sudoers.d/010_pi-nopasswd'
ssh pi@dashboard.local 'sudo -n true && echo NOPASSWD_OK'
```

Expected: `NOPASSWD_OK`. If you would rather not grant this, run every `sudo` step below
manually with `ssh -t` instead.

- [ ] **Step 1: Install packages and clone (idempotent)**

`python3-venv` is required by Step 2 and is not guaranteed. The clone is written to be
re-runnable — a plain `git clone` fails on a second attempt.

```bash
ssh pi@dashboard.local 'sudo apt-get update -qq && sudo apt-get install -y -qq git python3-venv'
ssh pi@dashboard.local 'if [ -d /home/pi/dashboard/.git ]; then git -C /home/pi/dashboard fetch --quiet origin && git -C /home/pi/dashboard reset --hard origin/main; else git clone --quiet https://github.com/luraschi8/homescreen.git /home/pi/dashboard; fi && git -C /home/pi/dashboard log --oneline -1'
```

- [ ] **Step 2: Create the Pi's venv**

`--system-site-packages` matters here and only here: the Pi needs apt's `lgpio`/`gpiozero`
in later phases (CLAUDE.md §2), and PEP 668 forbids installing outside a venv.

```bash
ssh pi@dashboard.local 'cd /home/pi/dashboard && python3 -m venv --system-site-packages venv && venv/bin/pip install --quiet -r requirements.txt && venv/bin/python -c "import flask, requests, yaml; print(\"deps ok\")"'
```

- [ ] **Step 3: Run the tests on the Pi**

The Mac is Python 3.14 and the Pi is 3.13; the suite must pass on the machine that runs it.

```bash
ssh pi@dashboard.local 'cd /home/pi/dashboard && venv/bin/pytest -q'
```

Expected: `83 passed`

- [ ] **Step 4: Keep the journal off the SD card**

Measured on this Pi: the journal is **entirely in RAM today** — every file under
`/run/log/journal`, `/var/log/journal` empty, `#Storage=auto` in `journald.conf`. Current
SD journal writes: **zero**.

That inverts the obvious fix. `Storage=persistent` would *start* writing to the card and
grant it a permanent rotating budget — creating the wear this step exists to prevent. And
`SystemMaxUse` governs `/var/log/journal`; the store actually in use is capped by
`RuntimeMaxUse`. So: pin it to RAM and cap the RAM store.

```bash
ssh pi@dashboard.local 'sudo mkdir -p /etc/systemd/journald.conf.d && printf "[Journal]\nStorage=volatile\nRuntimeMaxUse=32M\n" | sudo tee /etc/systemd/journald.conf.d/10-cap.conf && sudo systemctl restart systemd-journald'
ssh pi@dashboard.local 'journalctl --disk-usage && ls /var/log/journal'
```

Expected: usage reported against `/run/log/journal`, and `/var/log/journal` still empty.

**The trade:** logs do not survive a reboot. SPEC §12 chose systemd partly so
`journalctl -u` is useful at 1am, and that still works for a live fault — you lose only
post-mortem after a power cut. If you would rather keep history, `Storage=persistent`
with `SystemMaxUse=64M` is the alternative, and it is a deliberate decision against
CLAUDE.md §2's SD-wear line, not a mitigation of it.

- [ ] **Step 5: Install and start the units**

```bash
ssh pi@dashboard.local 'sudo cp /home/pi/dashboard/systemd/homescreen-*.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now homescreen-fetch.service homescreen-serve.service'
for i in $(seq 1 12); do
  ssh pi@dashboard.local 'systemctl is-active --quiet homescreen-fetch homescreen-serve' && break
  sleep 5
done
ssh pi@dashboard.local 'systemctl is-active homescreen-fetch homescreen-serve'
```

Expected: `active` twice. Poll rather than a fixed sleep — a `Restart=always` unit can be
caught mid-`activating`.

- [ ] **Step 6: Verify from the Mac, over the LAN**

```bash
curl -s "http://dashboard.local:8080/api/display/radar/data?rssi=-64&fw=0.1.0" \
  | python3 -m json.tool | head -20
curl -sI http://dashboard.local:8080/api/display/radar/data | grep -iE "etag|x-poll|x-feed"
curl -s http://dashboard.local:8080/api/display/radar/health | python3 -m json.tool
```

Expected: aircraft with `feed.ok: true` and a small `feed.age_s`; `ETag`, `X-Poll-Seconds`,
`X-Feed-Age` and `X-Feed-Ok` all present; health echoes the telemetry sent above.

- [ ] **Step 7: Verify 304 and graceful degradation**

The 304 check is deterministic only with the fetch daemon stopped — while it runs, the sky
genuinely changes every few seconds and a 200 is the correct answer.

```bash
ssh pi@dashboard.local 'sudo systemctl stop homescreen-fetch'          # Pi
# Assert the feed was healthy at the moment we captured the ETag: `fresh` also
# requires ok, so if the daemon's LAST cycle happened to fail, the first check
# below would return 200 for the wrong reason and look like a failure.
curl -sI http://dashboard.local:8080/api/display/radar/data | grep -qi 'x-feed-ok: 1' \
  || { echo "SKIP: feed was already unhealthy; restart the fetcher and retry"; }
ETAG=$(curl -sI http://dashboard.local:8080/api/display/radar/data \
       | awk -F'"' '/[Ee][Tt]ag/{print $2}')                            # Mac
curl -s -o /dev/null -w "unchanged content -> %{http_code}\n" \
  -H "If-None-Match: \"$ETAG\"" http://dashboard.local:8080/api/display/radar/data
sleep 14                                                                # past STALE_HORIZON_S
curl -s -o /dev/null -w "stale feed        -> %{http_code}\n" \
  -H "If-None-Match: \"$ETAG\"" http://dashboard.local:8080/api/display/radar/data
curl -s http://dashboard.local:8080/api/display/radar/data \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('served stale:', len(d['aircraft']), 'aircraft, feed.age_s', round(d['feed']['age_s'],1))"
ssh pi@dashboard.local 'sudo systemctl start homescreen-fetch'          # Pi
```

Expected: `304` for unchanged content, then **`200`** once past the 12 s horizon — a stale
feed must never be answered with a bodiless 304, or `feed.age_s` can never reach the device.
Aircraft are still served with a growing `feed.age_s` (SPEC §11.3).

- [ ] **Step 8: Copy the local secrets overlay, if one exists**

`config.local.yaml` is gitignored, so the clone in Step 1 cannot carry it. adsb.fi needs
no key, so this is a no-op today — but the moment a fetcher with credentials lands
(SPEC §7.4 Twelve Data, §7.2 the secret ICS URL) the overlay must reach the Pi or the Pi
runs on public config alone.

```bash
cd /Users/matias/Documents/repos/HomeScreen
if [ -f config.local.yaml ]; then
  scp config.local.yaml pi@dashboard.local:/home/pi/dashboard/config.local.yaml
  echo "overlay copied"
else
  echo "no config.local.yaml — nothing to copy (expected for Phase A)"
fi
```

- [ ] **Step 9: Verify survival across a reboot**

`sudo reboot` returns immediately and systemd takes seconds to tear down, while ssh
round-trip to this Pi is 0.2–0.9 s. A loop that polls `/health` straight away therefore
hits the **still-running pre-reboot server**, breaks, and reports success without a reboot
having happened. Gate on the boot id instead.

```bash
BEFORE=$(ssh pi@dashboard.local 'cat /proc/sys/kernel/random/boot_id')
ssh pi@dashboard.local 'sudo systemctl reboot' || true
AFTER=""
for i in $(seq 1 36); do
  sleep 5
  AFTER=$(ssh -o ConnectTimeout=5 -o BatchMode=yes pi@dashboard.local \
          'cat /proc/sys/kernel/random/boot_id' 2>/dev/null || true)
  [ -n "$AFTER" ] && [ "$AFTER" != "$BEFORE" ] && break
done
[ -n "$AFTER" ] && [ "$AFTER" != "$BEFORE" ] \
  || { echo "FAIL: Pi never rebooted (boot_id unchanged)"; exit 1; }

for i in $(seq 1 24); do
  curl -sf -o /dev/null http://dashboard.local:8080/api/display/radar/health && break
  sleep 5
done
curl -sf -o /dev/null http://dashboard.local:8080/api/display/radar/health \
  || { echo "FAIL: no /health within 2 min of a confirmed reboot"; exit 1; }
ssh pi@dashboard.local 'systemctl is-active homescreen-fetch homescreen-serve'
curl -s http://dashboard.local:8080/api/display/radar/health | python3 -m json.tool
```

Expected: a changed boot id, then `active` twice and a healthy feed, with no manual
intervention. Both loops are bounded — an unbounded wait would hang forever if the Pi
did not come back.

---

## Done when

- [ ] `venv/bin/pytest` is green on the Mac **and** on the Pi (83 tests)
- [ ] `curl http://dashboard.local:8080/api/display/radar/data` returns aircraft with `feed.ok: true`
- [ ] Served `age` and `feed.age_s` advance in real time while the fetch daemon is stopped
- [ ] A matching `If-None-Match` yields `304` for unchanged content **across a refetch**,
      and `200` once the feed is past the 12 s staleness horizon
- [ ] Stopping the fetch daemon leaves the endpoint serving stale data with a growing
      `feed.age_s`, never a 500; a garbage upstream response keeps the last good list
- [ ] `journalctl --disk-usage` is capped
- [ ] `/health` reports feed state and the last telemetry the device sent
- [ ] `systemctl is-active homescreen-fetch homescreen-serve` reports `active` after a reboot
- [ ] `X-Feed-Age` / `X-Feed-Ok` are present on a 304 as well as a 200

**Then, and only then**, start the firmware plan (Phase A4). ADDENDUM §0.5: do not begin
firmware work until the server path is proven end-to-end.

### Two things the firmware plan must carry

1. **On a 304 the device must not reset its fetch timestamp.** The device computes
   `age + secondsSinceUpdate()`. If a 304 resets that timer while the body is unchanged,
   ages freeze and dead reckoning stalls. Reset it only on a `200`.
2. **Read `X-Feed-Age` / `X-Feed-Ok`, not just the body.** They are set on the 304 as
   well as the 200 precisely because a 304 has no body — without them a device that
   conditional-fetches cannot see the feed's liveness at all.
3. **Staleness must be tested against `feed.age_s` (or `X-Feed-Age`), not the device's own fetch age.**
   Via the Pi the device's fetch keeps succeeding while the upstream rots. Keep
   `radar_display.cpp`'s two staleness causes tested *separately* — its comment records
   that summing them made targets blink once per cycle — and substitute `feed.age_s` for
   `fetch_age_raw`.
