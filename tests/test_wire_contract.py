"""The firmware parses these bytes. Renaming a field here is a blank screen on
hardware, so both sides pin the same fixture."""
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scripts.dump_wire_fixture import build          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "firmware" / "test" / "fixtures_wire.h"


def test_the_checked_in_fixture_still_matches_this_server(tmp_path):
    # Generated into tmp_path, NOT over the checked-in file. Regenerating in
    # place would mean run 1 fails, leaves the new bytes on disk, and run 2
    # passes -- with the firmware still parsing the old format. A guard that
    # heals itself on the second run is not a guard.
    fresh = tmp_path / "fixtures_wire.h"
    build(fresh)
    assert fresh.read_text() == FIXTURE.read_text(), (
        "the wire format changed and firmware/ still parses the old one. "
        "Regenerate: venv/bin/python scripts/dump_wire_fixture.py -- then "
        "update firmware/src/services/scene_client.cpp and re-run "
        "`pio test -e native` BEFORE committing.")


@pytest.mark.parametrize("field", ["lat", "lon", "nose", "trk", "gs", "ve", "vn",
                                   "age", "dst", "cs", "ty", "alt"])
def test_every_item_field_the_firmware_reads_is_present(field):
    assert f'"{field}"' in FIXTURE.read_text(), \
        f"scene_client.cpp reads {field!r} out of every item"


@pytest.mark.parametrize("key", ['"c":"radar"', '"components"', '"layout"',
                                 '"assigned"', '"radius_km"', '"feed_ok"',
                                 '"feed_age_s"', '"items"', '"scene"'])
def test_every_envelope_key_the_firmware_switches_on_is_present(key):
    assert key in FIXTURE.read_text()


def test_the_etag_fixture_is_valid_cpp_and_keeps_its_quotes():
    # The server's ETag already contains its HTTP quotes, so interpolating it
    # raw emits `= ""abc"";` -- which does not compile. It must be escaped, and
    # the quotes must survive: the firmware echoes them in If-None-Match.
    line = [l for l in FIXTURE.read_text().splitlines()
            if "kWireAssignedEtag" in l][0]
    m = re.search(r'= "((?:[^"\\]|\\.)*)";$', line)
    assert m, f"not a well-formed C++ string literal: {line}"
    assert m.group(1).startswith('\\"') and m.group(1).endswith('\\"'), \
        f"the HTTP quotes must be escaped and present: {line}"


def test_the_fixtures_cover_both_ways_a_feed_dies():
    # These are different failures and the firmware must treat them
    # differently: a transient must not blank the panel, a stopped daemon must.
    text = FIXTURE.read_text()
    down = [l for l in text.splitlines() if "kWireFeedDown" in l][0]
    stale = [l for l in text.splitlines() if "kWireFeedStale" in l][0]
    assert '"feed_ok":false' in down, "a transient: the fetch ran and failed"
    assert '"feed_ok":true' in stale, "a stopped daemon never writes a failure"
    assert '"feed_age_s":90.0' in stale, "...so only the age can show it"


def test_the_fixture_traffic_is_where_the_firmware_tests_look():
    # The renderer clips anything outside the outer ring. Fixture aircraft near
    # Amsterdam with a radar centred on Madrid are ~1,480 km out, never drawn,
    # and every drawing assertion passes vacuously.
    text = FIXTURE.read_text()
    assert "kWireHomeLat" in text and "kWireHomeLon" in text
    lat = float(re.search(r"kWireHomeLat = ([\d.-]+);", text).group(1))
    lon = float(re.search(r"kWireHomeLon = ([\d.-]+);", text).group(1))
    for item_lat in (float(m) for m in re.findall(r'"lat":([\d.-]+)', text)):
        assert abs(item_lat - lat) < 0.5, "a target is off the dial"
    for item_lon in (float(m) for m in re.findall(r'"lon":([\d.-]+)', text)):
        assert abs(item_lon - lon) < 0.5, "a target is off the dial"
