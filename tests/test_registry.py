# tests/test_registry.py
import json
import os
import threading
from pathlib import Path

import pytest

from homescreen import registry

HW = "a4cf12ab3c44"
CAPS = {"w": 240, "h": 240, "depth": 16, "layouts": ["fill"],
        "components": ["text", "rings", "markers"]}


# --- registration -----------------------------------------------------------

def test_first_contact_registers_an_unassigned_device(tmp_path: Path):
    rec = registry.touch(tmp_path, HW, fw="0.2.0", caps=CAPS, now=1000.0)
    assert rec["name"] is None, "unnamed until a human assigns one"
    assert rec["scene"] == "unassigned"
    assert rec["fw"] == "0.2.0"
    assert rec["caps"] == CAPS
    assert registry.load(tmp_path)[HW]["fw"] == "0.2.0"


def test_later_contact_updates_without_losing_assignment(tmp_path: Path):
    registry.touch(tmp_path, HW, fw="0.2.0", caps=CAPS, now=1000.0)
    registry.assign(tmp_path, HW, name="radar", scene="planes")
    rec = registry.touch(tmp_path, HW, fw="0.3.0",
                         telemetry={"rssi": "-64"}, now=2000.0)
    assert rec["name"] == "radar", "a reflash must not orphan the device"
    assert rec["scene"] == "planes"
    assert rec["fw"] == "0.3.0"


def test_capabilities_merge_rather_than_replace(tmp_path: Path):
    # Phase 2 builds a scene from `components`; a device reporting one field
    # later must not erase the rest.
    registry.touch(tmp_path, HW, caps=CAPS, now=1000.0)
    rec = registry.touch(tmp_path, HW, caps={"w": 240}, now=1000.0)
    assert rec["caps"]["components"] == ["text", "rings", "markers"]


@pytest.mark.parametrize("hw", ["", "   ", "has/slash", "x" * 200, None, 5,
                                "sp ace", "semi;colon"])
def test_an_unusable_hardware_id_is_rejected_not_registered(tmp_path, hw):
    with pytest.raises(ValueError):
        registry.touch(tmp_path, hw, now=1000.0)
    assert registry.load(tmp_path) == {}


# --- liveness ---------------------------------------------------------------

def test_liveness_is_derived_from_last_seen(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0)
    registry.assign(tmp_path, HW, poll_seconds=5)
    rec = registry.load(tmp_path)[HW]
    assert registry.is_online(rec, 1010.0) is True, "2 polls late is fine"
    assert registry.is_online(rec, 1020.0) is False, "3x the interval is offline"
    assert "online" not in rec, "derived, never stored"


def test_a_future_stamp_is_clock_skew_not_freshness(tmp_path: Path):
    # A Pi 4 has no RTC: it boots at the time timesyncd last saved and jumps
    # when NTP lands, so a pre-sync stamp sits ahead of us. serve._feed_state
    # already refuses this; without the mirror guard a dead device reads
    # online forever.
    registry.touch(tmp_path, HW, now=2_000_000_000.0)
    assert registry.is_online(registry.load(tmp_path)[HW], 1000.0) is False


@pytest.mark.parametrize("rec", [
    {}, {"last_seen": None}, {"last_seen": "garbage"}, "not a dict", None,
    {"last_seen": "2026-01-01T00:00:00+00:00", "poll_seconds": "x"},
])
def test_liveness_never_raises_on_a_damaged_record(rec):
    assert registry.is_online(rec, 1000.0) in (True, False)


# --- assignment -------------------------------------------------------------

@pytest.mark.parametrize("kwargs,why", [
    ({"name": ""}, "empty"),
    ({"name": "   "}, "whitespace"),
    ({"name": "has/slash"}, "would break the URL alias"),
    ({"name": "a" * 65}, "absurd length"),
    ({"scene": "no-such-scene"}, "unknown scene"),
    ({"scene": "error"}, "a server-chosen fallback, not an assignment"),
    ({"scene": "unassigned"}, "same"),
    ({"poll_seconds": 0}, "would make liveness meaningless"),
    ({"poll_seconds": "soon"}, "not a number"),
    ({"poll_seconds": 99999}, "out of range"),
])
def test_assign_rejects_bad_values_without_persisting(tmp_path, kwargs, why):
    registry.touch(tmp_path, HW, now=1000.0)
    before = registry.load(tmp_path)[HW]
    with pytest.raises(ValueError):
        registry.assign(tmp_path, HW, **kwargs)
    assert registry.load(tmp_path)[HW] == before, f"{why}: nothing reaches disk"


def test_names_must_be_unique(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0)
    registry.touch(tmp_path, "ffff0000ffff", now=1000.0)
    registry.assign(tmp_path, HW, name="radar")
    with pytest.raises(ValueError, match="already"):
        registry.assign(tmp_path, "ffff0000ffff", name="radar")
    registry.assign(tmp_path, HW, name="radar")   # same device: not a collision


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


# --- storage discipline: this is what makes it different from overrides.py ---

@pytest.mark.parametrize("junk", [
    "{not json", "[]", '"a string"', "null", "5",
    '{"hw": "not a record"}', '{"hw": [1,2]}',
])
def test_a_corrupt_registry_degrades_to_empty(tmp_path: Path, junk):
    registry.registry_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    registry.registry_path(tmp_path).write_text(junk)
    assert registry.load(tmp_path) == {}


def test_a_corrupt_registry_is_quarantined_not_overwritten(tmp_path: Path):
    # load() degrading to {} plus a later save() is DATA LOSS, not degradation:
    # every real assignment vanishes on the next restart.
    registry.touch(tmp_path, HW, now=1000.0)
    registry.assign(tmp_path, HW, name="kitchen", scene="planes")
    registry.registry_path(tmp_path).write_text("{truncated mid-writ")
    registry.touch(tmp_path, "ffff0000ffff", now=1000.0)      # a write happens
    saved = list(tmp_path.glob("devices.corrupt-*"))
    assert len(saved) == 1, "the damaged file must be preserved for inspection"
    assert saved[0].read_text() == "{truncated mid-writ", \
        "kept verbatim -- quarantine is forensics, not recovery"
    # And the live registry is a clean file containing only the new device,
    # rather than a silent overwrite of whatever was there.
    assert list(registry.load(tmp_path)) == ["ffff0000ffff"]


@pytest.mark.parametrize("bad", [
    {"name": 5}, {"scene": 7}, {"poll_seconds": "soon"}, {"caps": "none"},
    {"telemetry": []}, {"first_seen": None},
])
def test_parseable_but_wrong_typed_records_are_dropped(tmp_path: Path, bad):
    # Both consumers index these unguarded; a wrong type 500s the serve path.
    good = {"name": "a", "scene": "planes", "fw": "1",
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-01T00:00:00+00:00",
            "caps": {}, "telemetry": {}, "poll_seconds": 5}
    registry.registry_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    registry.registry_path(tmp_path).write_text(
        json.dumps({"good": good, "bad": {**good, **bad}}))
    assert list(registry.load(tmp_path)) == ["good"]


def test_one_request_cannot_write_an_unbounded_file(tmp_path: Path):
    # Unauthenticated network input on a microSD CLAUDE.md flags as unmitigated.
    registry.touch(tmp_path, HW, now=1000.0,
                   telemetry={f"k{i}": "v" * 500 for i in range(500)},
                   caps={"w": 99999999999999, "depth": -5,
                         "components": ["x"] * 500})
    rec = registry.load(tmp_path)[HW]
    assert len(rec["telemetry"]) <= registry.MAX_TELEMETRY_KEYS
    assert all(len(v) <= registry.MAX_VALUE_LEN for v in rec["telemetry"].values())
    assert "w" not in rec["caps"] and "depth" not in rec["caps"], "out of range"
    assert len(rec["caps"]["components"]) <= registry.MAX_CAP_LIST
    assert os.path.getsize(registry.registry_path(tmp_path)) < 8000


def test_the_device_count_is_bounded(tmp_path: Path):
    for i in range(registry.MAX_DEVICES):
        registry.touch(tmp_path, f"{i:012x}", now=1000.0)
    with pytest.raises(ValueError, match="full"):
        registry.touch(tmp_path, "ffffffffffff", now=1000.0)


def test_repeat_polls_do_not_rewrite_the_card(tmp_path: Path):
    # ~17k writes/device/day otherwise -- the wear pattern cache.write_failure
    # already refuses.
    registry.touch(tmp_path, HW, telemetry={"rssi": "-64"}, now=1000.0)
    before = os.stat(registry.registry_path(tmp_path)).st_mtime_ns
    for i in range(20):
        registry.touch(tmp_path, HW, telemetry={"rssi": "-64"}, now=1000.0 + i)
    assert os.stat(registry.registry_path(tmp_path)).st_mtime_ns == before

    registry.touch(tmp_path, HW, telemetry={"rssi": "-70"}, now=1001.0)
    changed = os.stat(registry.registry_path(tmp_path)).st_mtime_ns
    assert changed != before, "a real change must still persist"

    registry.touch(tmp_path, HW, telemetry={"rssi": "-70"}, now=1200.0)
    assert os.stat(registry.registry_path(tmp_path)).st_mtime_ns != changed, \
        "and a stale stamp must eventually refresh"


def test_a_poll_and_a_patch_do_not_lose_each_other(tmp_path: Path):
    # Both are read-modify-write and Flask serves them on different threads.
    # Locking only the write loses whichever saved first.
    registry.touch(tmp_path, HW, fw="0.1.0", now=1000.0)
    errors = []

    def poll():
        try:
            for i in range(50):
                registry.touch(tmp_path, HW, fw="0.3.0",
                               telemetry={"n": str(i)}, now=1000.0 + i * 60)
        except Exception as exc:            # noqa: BLE001
            errors.append(repr(exc))

    def patch():
        try:
            for _ in range(50):
                registry.assign(tmp_path, HW, scene="planes")
        except Exception as exc:            # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=poll), threading.Thread(target=patch),
               threading.Thread(target=poll), threading.Thread(target=patch)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rec = registry.load(tmp_path)[HW]
    assert not errors, errors[:2]
    assert rec["fw"] == "0.3.0", "the poll's update survived"
    assert rec["scene"] == "planes", "and so did the human's"


# --- migration --------------------------------------------------------------

CFG_WITH_DEVICE = {"devices": [
    {"id": "radar", "kind": "gc9a01_client", "render": "device",
     "feed": "adsb", "poll_seconds": 5, "scene": "planes"}]}


def test_seeding_carries_the_name_and_scene_from_config(tmp_path: Path):
    assert registry.seed_from_config(CFG_WITH_DEVICE, tmp_path, now=1000.0) == 1
    rec = registry.load(tmp_path)["cfg:radar"]
    assert rec["name"] == "radar"
    assert rec["scene"] == "planes"
    assert rec["fw"] == "config"


def test_seeding_runs_once_and_a_deletion_is_durable(tmp_path: Path):
    # Spec 4.7: config is "read once and never again". Re-seeding would
    # resurrect a record deleted while adopting the real board, leaving two
    # devices with the same name -- written by a path that bypasses _check_name.
    registry.seed_from_config(CFG_WITH_DEVICE, tmp_path, now=1000.0)
    registry.forget(tmp_path, "cfg:radar")
    assert registry.seed_from_config(CFG_WITH_DEVICE, tmp_path, now=2000.0) == 0
    assert registry.load(tmp_path) == {}, "deletion must stick across a restart"


def test_seeding_never_writes_a_duplicate_name(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0)
    registry.assign(tmp_path, HW, name="radar")
    registry.seed_from_config(CFG_WITH_DEVICE, tmp_path, now=1000.0)
    names = [r["name"] for r in registry.load(tmp_path).values()]
    assert names.count("radar") == 1, "the seeded record yields, unnamed"


def test_seeding_defaults_to_unassigned_without_a_scene_key(tmp_path: Path):
    registry.seed_from_config({"devices": [{"id": "x"}]}, tmp_path, now=1000.0)
    assert registry.load(tmp_path)["cfg:x"]["scene"] == "unassigned"


@pytest.mark.parametrize("cfg", [
    {}, {"devices": None}, {"devices": "radar"}, {"devices": [None, 5, {"no_id": 1}]},
])
def test_seeding_survives_a_malformed_devices_list(tmp_path: Path, cfg):
    assert registry.seed_from_config(cfg, tmp_path, now=1000.0) == 0
    assert registry.load(tmp_path) == {}


def test_the_seed_marker_is_not_a_phantom_device(tmp_path: Path):
    # Stored as a record it would pass _valid_record and render in the fleet.
    registry.seed_from_config(CFG_WITH_DEVICE, tmp_path, now=1000.0)
    assert list(registry.load(tmp_path)) == ["cfg:radar"]


def test_a_poll_that_omits_fw_does_not_record_the_string_none(tmp_path: Path):
    # touch() guards this and nothing exercised it: without the guard every
    # device call that omits the parameter would write fw="None".
    registry.touch(tmp_path, HW, fw="0.2.0", now=1000.0)
    rec = registry.touch(tmp_path, HW, now=1000.0)
    assert rec["fw"] == "0.2.0", "an absent fw must not overwrite a known one"
    assert registry.touch(tmp_path, "bb", now=1000.0)["fw"] is None


def test_bounds_are_literal_not_self_referential(tmp_path: Path):
    # The other bound tests loop over the constant, so they move with it --
    # MAX_DEVICES x10 survived mutation for exactly that reason. Pin the values.
    assert registry.MAX_DEVICES == 64
    assert registry.MAX_TELEMETRY_KEYS == 16
    assert registry.MAX_VALUE_LEN == 128
    assert registry.MAX_CAP_LIST == 32
    assert registry.OFFLINE_AFTER_POLLS == 3
    assert registry.DEFAULT_POLL_SECONDS == 5
    assert registry.CAP_INT_RANGE == (1, 4096)


def test_a_telemetry_key_longer_than_32_chars_is_dropped(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0,
                   telemetry={"k" * 40: "v", "ok": "1"})
    keys = registry.load(tmp_path)[HW]["telemetry"]
    assert "ok" in keys
    assert all(len(k) <= 32 for k in keys)


def test_a_hardware_id_is_stored_stripped(tmp_path: Path):
    registry.touch(tmp_path, "  aabbcc  ", now=1000.0)
    assert list(registry.load(tmp_path)) == ["aabbcc"]


def test_a_name_of_exactly_the_maximum_length_is_allowed(tmp_path: Path):
    registry.touch(tmp_path, HW, now=1000.0)
    registry.assign(tmp_path, HW, name="n" * registry.NAME_MAX)
    with pytest.raises(ValueError):
        registry.assign(tmp_path, HW, name="n" * (registry.NAME_MAX + 1))
