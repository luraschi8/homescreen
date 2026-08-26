# tests/test_adsb.py
import copy
import json
from pathlib import Path

import pytest

from homescreen.cache import read_cache, write_cache
from homescreen.config import (device, feed_cache_path, feed_config,
                               load_config)
from homescreen.sources.adsb import (check_cadence, check_config, fetch_radar,
                                    fetch_targets, run_forever, startup)

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
    assert env["error"] == "response is not the aircraft feed", (
        "the deliberate refusal must not decay into a generic mapping error")
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


def test_failure_recording_that_itself_fails_does_not_raise(tmp_path: Path, monkeypatch):
    # A read-only SD card is the canonical Pi failure and makes every write
    # throw. If recording the failure raises, run_forever exits and
    # Restart=always turns it into a restart loop.
    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr("homescreen.sources.adsb.write_failure", boom)
    s = FakeSession(exc=OSError("no route to host"))
    assert fetch_radar(CFG, DEV, tmp_path / "radar.json", session=s) is False


def test_fetch_targets_is_one_entry_per_feed_not_per_device(tmp_path: Path):
    # The cache is feed-keyed, so two screens on one feed is ONE upstream
    # request -- not two racing into the same file against a 1 req/s limit.
    cfg = {"devices": [
        {"id": "radar", "render": "device", "feed": "adsb"},
        {"id": "radar2", "render": "device", "feed": "adsb"},    # same feed
        {"id": "kitchen", "render": "server", "feed": "adsb"},   # pixel push
        {"id": "other", "render": "device", "feed": "weather"},  # not ours
    ]}
    got = [(d["id"], p.name) for d, p in fetch_targets(cfg, tmp_path)]
    # One entry: both adsb devices share a feed, and `weather` belongs to a
    # fetcher that does not exist yet -- this module only knows adsb.
    assert got == [("radar", "adsb.json")]


# Every shape a hand-edited config.yaml realistically takes. `feeds:` with its
# children commented out is the most ordinary of them, and it is the one that
# `.get(k, {})` does NOT defend against -- the default fires only on an ABSENT
# key, never on a present-but-null one.
BAD_FEEDS = [
    {},                                   # no feeds key at all
    {"feeds": None},                      # feeds:
    {"feeds": {}},                        # feeds: {}
    {"feeds": {"adsb": None}},            # feeds:\n  adsb:
    {"feeds": ["adsb"]},                  # a list
    {"feeds": "adsb"},                    # a string
    {"feeds": {"adsb": "https://x"}},     # feed is a string
    {"feeds": {"adsb": ["x"]}},           # feed is a list
]


@pytest.mark.parametrize("cfg", BAD_FEEDS)
def test_check_config_rejects_a_feeds_block_with_no_usable_endpoint(cfg):
    # Coercion alone would leave the unit active and green while every fetch
    # fails. Anything uncoercible must be EX_CONFIG at startup instead.
    with pytest.raises(ValueError, match="endpoint"):
        check_config(cfg, [({"id": "r", "home": {"lat": 1, "lon": 2}}, Path("x"))])


@pytest.mark.parametrize("dev", [
    {"id": "r"},                                        # no home at all
    {"id": "r", "home": "madrid"},
    {"id": "r", "home": {"lat": 1}},                    # no lon
    {"id": "r", "home": {"lat": "north", "lon": 2}},
    {"id": "r", "home": {"lat": 1, "lon": 2}, "radius_km": "60 km"},
    {"id": "r", "home": {"lat": 1, "lon": 2}, "max_aircraft": "twenty"},
])
def test_check_config_rejects_unusable_device_settings(dev):
    cfg = {"feeds": {"adsb": {"endpoint": "https://x", "fetch_seconds": 3}}}
    with pytest.raises(ValueError):
        check_config(cfg, [(dev, Path("x"))])


@pytest.mark.parametrize("dev", [
    {"radius_km": 0},                        # fetches nothing, forever, unit stays green
    {"radius_km": -5},
    {"radius_km": float("inf")},             # produces a nonsense dist=inf request
    {"max_aircraft": 0},                     # serves nothing
    {"home": {"lat": 95, "lon": -3.7}},      # not a latitude
    {"home": {"lat": 40.4, "lon": 400}},     # not a longitude
    {"home": {"lat": float("nan"), "lon": -3.7}},
])
def test_check_config_rejects_out_of_range_and_non_finite(dev):
    cfg = {"feeds": {"adsb": {"endpoint": "https://x", "fetch_seconds": 3}}}
    base = {"id": "r", "home": {"lat": 40.4, "lon": -3.7}}
    with pytest.raises(ValueError, match=r"radius_km|max_aircraft|home\."):
        check_config(cfg, [({**base, **dev}, Path("x"))])


@pytest.mark.parametrize("dev", [
    {"radius_km": 60, "max_aircraft": 20},          # the shipped values
    {"radius_km": 60.5, "max_aircraft": 1},         # float radius, minimum cap
    {"home": {"lat": 0, "lon": 0}},                 # null island is a real place
    {"home": {"lat": -90, "lon": 180}},             # the corners are valid
    {"home": {"lat": 40.4, "lon": -3.7, "note": "extra keys are fine"}},
])
def test_check_config_accepts_legitimate_values(dev):
    cfg = {"feeds": {"adsb": {"endpoint": "https://x", "fetch_seconds": 3}}}
    base = {"id": "r", "home": {"lat": 40.4, "lon": -3.7}}
    check_config(cfg, [({**base, **dev}, Path("x"))])


GOOD_CONFIG = """
devices:
  - {id: radar, kind: gc9a01_client, render: device, feed: adsb,
     home: {lat: 40.4168, lon: -3.7038}, radius_km: 60, max_aircraft: 20,
     poll_seconds: 5}
feeds:
  adsb: {source: api, endpoint: "https://example.invalid/api", fetch_seconds: 3}
"""


def _root(tmp_path: Path, text: str) -> Path:
    (tmp_path / "config.yaml").write_text(text)
    return tmp_path


def test_startup_returns_config_and_targets_for_a_good_config(tmp_path: Path):
    cfg, targets = startup(_root(tmp_path, GOOD_CONFIG))
    assert [d["id"] for d, _ in targets] == ["radar"]
    assert cfg["feeds"]["adsb"]["fetch_seconds"] == 3


@pytest.mark.parametrize("text,why", [
    ("devices: [\n", "malformed YAML"),
    ("", "empty file"),
    ("- a\n- b\n", "top-level list, not a mapping"),
    ("devices: []\nfeeds: {adsb: {endpoint: x}}\n", "no matching targets"),
    ("devices:\n  - {id: r, render: device, feed: adsb, home: {lat: 1, lon: 2}}\nfeeds:\n",
     "feeds present but null"),
    ("devices:\n  - {id: r, render: device, feed: adsb, home: madrid}\nfeeds: {adsb: {endpoint: x}}\n",
     "home is a string"),
    ("devices:\n  - {id: r, render: device, feed: adsb, home: {lat: 1, lon: 2}, radius_km: 0}\nfeeds: {adsb: {endpoint: x}}\n",
     "zero radius fetches nothing forever"),
    ("devices:\n  - {id: r, render: device, feed: adsb, home: {lat: 1, lon: 2}, poll_seconds: }\nfeeds: {adsb: {endpoint: x}}\n",
     "dangling poll_seconds"),
])
def test_startup_exits_78_on_every_config_fault(tmp_path: Path, text, why):
    # This test exists because six mutations to main()'s guard once survived
    # the entire suite -- including shrinking the try back to load_config only,
    # which silently re-introduces a Restart=always traceback loop.
    with pytest.raises(SystemExit) as exc:
        startup(_root(tmp_path, text))
    assert exc.value.code == 78, why


def test_startup_does_not_swallow_a_systemexit_from_inside(tmp_path, monkeypatch):
    # `except Exception` must not catch BaseException, or a deliberate exit
    # would be relabelled EX_CONFIG.
    def boom(*a, **k):
        raise SystemExit(3)

    monkeypatch.setattr("homescreen.sources.adsb.check_config", boom)
    with pytest.raises(SystemExit) as exc:
        startup(_root(tmp_path, GOOD_CONFIG))
    assert exc.value.code == 3


def test_check_config_rejects_a_target_with_no_usable_id():
    # Defence in depth: fetch_targets already filters these out, so without a
    # direct test the guard is unreachable and its removal goes unnoticed.
    cfg = {"feeds": {"adsb": {"endpoint": "https://x", "fetch_seconds": 3}}}
    with pytest.raises(ValueError, match="no usable id"):
        check_config(cfg, [({"home": {"lat": 1, "lon": 2}}, Path("x"))])


def test_check_config_passes_the_real_target_count_to_check_cadence():
    # check_cadence is tested directly with n=2, but nothing checked that
    # check_config forwards len(targets) rather than a hardcoded 1 -- so a
    # second radar would sail through startup and then blink once per cycle.
    cfg = {"feeds": {"adsb": {"endpoint": "https://x", "fetch_seconds": 3}}}
    dev = {"id": "radar", "home": {"lat": 40.4, "lon": -3.7},
           "radius_km": 60, "max_aircraft": 20, "poll_seconds": 5}
    check_config(cfg, [(dev, Path("a"))])                      # N=1 is fine
    with pytest.raises(ValueError, match="horizon"):
        check_config(cfg, [(dev, Path("a")), ({**dev, "id": "radar2"}, Path("b"))])


def test_check_config_accepts_the_shipped_shape():
    cfg = {"feeds": {"adsb": {"endpoint": "https://x", "fetch_seconds": 3}}}
    dev = {"id": "radar", "home": {"lat": 40.4168, "lon": -3.7038},
           "radius_km": 60, "max_aircraft": 20}
    check_config(cfg, [(dev, Path("x"))])


@pytest.mark.parametrize("cfg", BAD_FEEDS)
def test_check_cadence_survives_every_malformed_feeds_shape(cfg):
    # check_cadence runs FIRST in main(), before fetch_radar. Hardening
    # fetch_radar alone leaves the daemon dying before it is ever reached.
    check_cadence(cfg, 1)          # falls back to the default interval


@pytest.mark.parametrize("cfg", BAD_FEEDS)
def test_fetch_radar_survives_every_malformed_feeds_shape(tmp_path, cfg):
    # The property is "never raises", not "always fails": a coerced feeds block
    # legitimately falls back to the default endpoint and carries on. What must
    # not happen is an exception escaping into run_forever.
    p = tmp_path / "r.json"
    s = FakeSession(FakeResponse({"ac": []}))
    assert fetch_radar(cfg, {"id": "r"}, p, session=s) in (True, False)
    assert read_cache(p) is not None, "a valid envelope either way"


@pytest.mark.parametrize("cfg", BAD_FEEDS)
def test_run_forever_survives_every_malformed_feeds_shape(tmp_path, cfg):
    class Stop(BaseException):
        pass

    class OneShot:
        def get(self, url, timeout=None):
            raise Stop

    with pytest.raises(Stop):
        run_forever(cfg, [({"id": "r"}, tmp_path / "r.json")],
                    session=OneShot(), sleep=lambda d: None)


def test_mapping_coerces_present_but_null_and_wrong_types():
    from homescreen.config import feed_config, mapping, server_config
    assert mapping(None) == {} and mapping([1]) == {} and mapping("x") == {}
    assert mapping({"a": 1}) == {"a": 1}
    assert feed_config({"feeds": None}) == {}
    assert feed_config({"feeds": {"adsb": None}}) == {}
    assert feed_config({"feeds": {"adsb": {"k": 1}}}) == {"k": 1}
    assert server_config({"server": None}) == {}


def test_check_cadence_rejects_an_interval_past_the_horizon():
    check_cadence({"feeds": {"adsb": {"fetch_seconds": 3}}})   # 8.05+3 = 11.05 < 12
    with pytest.raises(ValueError, match="horizon"):
        check_cadence({"feeds": {"adsb": {"fetch_seconds": 5}}})  # 13.05 >= 12


def test_check_cadence_scales_the_budget_with_device_count():
    # run_forever fetches each target and sleeps interval/N between them, so a
    # cycle costs N x request + interval. A single-target budget would give a
    # false all-clear the moment a second radar is registered.
    cfg = {"feeds": {"adsb": {"fetch_seconds": 3}}}
    check_cadence(cfg, 1)
    with pytest.raises(ValueError, match="horizon"):
        check_cadence(cfg, 2)          # 8.05*2 + 3 = 19.1 >= 12


def test_check_cadence_rejects_bursting_past_the_rate_limit(monkeypatch):
    # adsb.fi enforces spacing, not an average rate. Shrink the timeout to
    # isolate this check: at the shipped (3.05, 5) the horizon check fires
    # first for every N > 1, so the rate check would be unreachable.
    monkeypatch.setattr("homescreen.sources.adsb.REQUEST_TIMEOUT_S", (0.5, 0.5))
    check_cadence({"feeds": {"adsb": {"fetch_seconds": 3}}}, 3)   # 1.0s apart, ok
    with pytest.raises(ValueError, match="limit"):
        check_cadence({"feeds": {"adsb": {"fetch_seconds": 3}}}, 4)  # 0.75s apart


@pytest.mark.parametrize("dev", [
    {"id": "r", "radius_km": "60 km"},     # a units typo a human writes
    {"id": "r", "radius_km": ["60"]},
    {"id": "r", "home": "madrid"},
    {"id": "r", "home": {"lat": "north"}},
    {"id": "r", "max_aircraft": "twenty"},
])
def test_unusable_device_config_is_a_failure_not_an_escaping_exception(tmp_path, dev):
    # These cannot be coerced to anything sensible, so they must be RECORDED as
    # failures. Reading them outside fetch_radar's try meant they escaped into
    # run_forever, which has no handler because the docstring says it cannot
    # happen -- and the unit then restart-looped on exit 1 instead of EX_CONFIG.
    cfg = {"feeds": {"adsb": {"endpoint": "x"}}}
    s = FakeSession(FakeResponse({"ac": []}))
    assert fetch_radar(cfg, dev, tmp_path / "radar.json", session=s) is False
    assert read_cache(tmp_path / "radar.json")["ok"] is False


def test_run_forever_spaces_requests_across_the_cycle(tmp_path: Path):
    # check_cadence's budget is only correct BECAUSE of this loop's shape.
    # Without a test here, changing the sleep to `interval` (bursting N
    # requests, breaching adsb.fi's 1 req/s) passes the whole suite.
    # Bound the loop on the SESSION, not on sleep: bounding it on sleep means a
    # mutation that deletes the sleep spins forever instead of failing.
    # BaseException so fetch_radar's `except Exception` cannot swallow it.
    class Stop(BaseException):
        pass

    slept = []

    class CountingSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, timeout=None):
            self.calls += 1
            if self.calls > 4:
                raise Stop
            return FakeResponse({"ac": []})

    cfg = {"feeds": {"adsb": {"endpoint": "https://example.invalid/api",
                              "fetch_seconds": 3}},
           "devices": []}
    devs = [{"id": "a", "home": {}, "radius_km": 60, "max_aircraft": 20},
            {"id": "b", "home": {}, "radius_km": 60, "max_aircraft": 20}]
    targets = [(d, tmp_path / f"{d['id']}.json") for d in devs]
    sess = CountingSession()
    with pytest.raises(Stop):
        run_forever(cfg, targets, session=sess, sleep=slept.append)
    # interval 3 / 2 targets = 1.5s after each of the first four requests.
    assert slept == [1.5, 1.5, 1.5, 1.5], "interval/N after EACH request"
    assert sess.calls == 5, "requests are spaced, not burst then slept once"


@pytest.mark.timeout(5)
def test_run_forever_refuses_an_empty_target_list():
    # Otherwise the for-body never runs and the while spins at 100% CPU with no
    # requests and no sleeps. The explicit timeout marker is what makes removing
    # the guard show up as a failure rather than a hung suite.
    with pytest.raises(ValueError, match="at least one target"):
        run_forever({"feeds": {"adsb": {"fetch_seconds": 3}}}, [])


def test_check_cadence_reports_a_non_numeric_interval_as_valueerror():
    with pytest.raises(ValueError, match="must be a number"):
        check_cadence({"feeds": {"adsb": {"fetch_seconds": None}}})


def test_fetch_targets_skips_entries_with_no_id(tmp_path: Path):
    # feed_cache_path does dev["id"]; an unguarded KeyError here is a startup
    # traceback into a Restart=always loop.
    cfg = {"devices": [{"render": "device", "feed": "adsb"},
                       {"id": "radar", "render": "device", "feed": "adsb"}]}
    assert [d["id"] for d, _ in fetch_targets(cfg, tmp_path)] == ["radar"]


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
    assert feed_cache_path(tmp_path, dev) == tmp_path / "feed" / "adsb.json"
    # Per FEED, not per device: a self-registered device declares a screen
    # size, not a location, so two screens on one feed share the data.
    assert (feed_cache_path(tmp_path, {"id": "radar2", "feed": "adsb"})
            == tmp_path / "feed" / "adsb.json")
    assert (feed_cache_path(tmp_path, {"id": "x", "feed": "weather"})
            == tmp_path / "feed" / "weather.json")
    # A device naming no feed falls back rather than raising KeyError.
    assert feed_cache_path(tmp_path, {}) == tmp_path / "feed" / "adsb.json"
    assert feed_cache_path(tmp_path, None) == tmp_path / "feed" / "adsb.json"
