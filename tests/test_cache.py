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


def test_read_rejects_bare_infinity_and_nan(tmp_path: Path):
    # json.load's default parse_constant returns inf/nan happily. Bare
    # Infinity/NaN is not strict JSON and the firmware's parser rejects the
    # ENTIRE body over one, so a poisoned cache must read as no-data.
    for bad in ("Infinity", "-Infinity", "NaN"):
        p = tmp_path / "bad.json"
        p.write_text('{"fetched_at":"2026-01-01T00:00:00+00:00","ok":true,'
                     '"data":{"aircraft":[{"gs":%s}]}}' % bad)
        assert read_cache(p) is None, f"{bad} must not survive a read"


def test_read_missing_returns_none(tmp_path: Path):
    assert read_cache(tmp_path / "nope.json") is None


def test_read_corrupt_returns_none(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert read_cache(p) is None


def test_read_rejects_a_non_finite_written_as_a_plain_number(tmp_path: Path):
    # parse_constant only fires for the literals Infinity/NaN. 1e400 is
    # ordinary JSON that float() turns into inf -- the very form the mapper
    # guards against upstream.
    p = tmp_path / "bad.json"
    p.write_text('{"fetched_at":"2026-01-01T00:00:00+00:00","ok":true,'
                 '"data":{"aircraft":[{"lat":1e400}]}}')
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
    assert not p.exists(), "no cache file"
    assert list(tmp_path.iterdir()) == [], "and no .tmp residue either"


def test_write_leaves_no_temp_file(tmp_path: Path):
    p = tmp_path / "radar.json"
    write_cache(p, {"a": 1})
    assert list(tmp_path.iterdir()) == [p]
