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
