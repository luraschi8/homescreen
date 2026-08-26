# tests/test_serve.py
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from homescreen import registry as registry_mod
from homescreen.cache import write_cache, write_failure
from homescreen.serve import create_app, startup as serve_startup

CFG = {
    "feeds": {"adsb": {"source": "api"}},
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
    return app.test_client(), tmp_path / "feed" / "adsb.json", clock


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
    _seed(path)                    # a real fetch: the dwell clock restarts
    clock.t += 1.5                 # ...and the record then sits here this long
    r304 = client.get("/api/display/radar/data",
                      headers={"If-None-Match": r200.headers["ETag"]})
    assert r304.status_code == 304
    assert r304.headers["X-Feed-Ok"] == "1"
    # `>= 0.0` was inert: _feed_state returns max(0.0, delta), so no emittable
    # value could fail it. Assert the number the device would actually act on.
    assert float(r304.headers["X-Feed-Age"]) == pytest.approx(1.5, abs=0.05)


def test_etag_changes_when_the_cache_changes(ctx):
    client, path, clock = ctx
    _seed(path, age=2.0)
    a = client.get("/api/display/radar/data").headers["ETag"]
    clock.t += 1.0
    _seed(path, age=9.0)
    b = client.get("/api/display/radar/data").headers["ETag"]
    assert a != b


def test_feed_source_reports_the_provider_not_the_config_mode(ctx):
    # ADDENDUM §6's `source` is a mode (api | dump1090); PLAN.md §3's
    # feed.source is the provider. "api" is never a provider name.
    client, path, _ = ctx
    _seed(path)
    body = client.get("/api/display/radar/data").get_json()
    assert body["feed"]["source"] == "adsb.fi"


def test_health_answers_for_a_pixel_push_device_too(tmp_path):
    # ADDENDUM §5 defines /health for every device, unconditionally.
    cfg = {"devices": [{"id": "kitchen", "kind": "epaper_client",
                        "render": "server", "feed": "adsb"}]}
    client = create_app(cfg, tmp_path).test_client()
    body = client.get("/api/display/kitchen/health").get_json()
    assert body["render"] == "server"
    # Not `ok: false` -- it has no ADS-B feed at all, and claiming a dead feed
    # is a misleading answer on a debugging endpoint.
    assert body["feed"] is None
    assert client.get("/api/display/kitchen/data").status_code == 404


@pytest.mark.parametrize("cfg", [
    {"feeds": ["adsb"], "devices": [{"id": "radar", "render": "device"}]},
    {"feeds": {"adsb": "https://x"}, "devices": [{"id": "radar", "render": "device"}]},
    {"feeds": {"adsb": {"source": "api"}}, "devices": "radar"},
    {"feeds": {"adsb": {"source": "api"}}, "devices": 5},
])
def test_malformed_config_never_500s_the_serve_path(tmp_path, cfg):
    # "Nothing may raise into the serve path. Ever." -- including config that
    # is the wrong shape one level above where the value guard sits.
    client = create_app(cfg, tmp_path).test_client()
    for path in ("/api/display/radar/data", "/api/display/radar/health"):
        assert client.get(path).status_code in (200, 404), f"{path} on {cfg!r}"


@pytest.mark.parametrize("srv", [None, "0.0.0.0:8080", ["0.0.0.0"], {}, {"port": "eighty"}])
def test_server_block_shapes_are_resolved_or_rejected_not_crashed(srv):
    # `server:` with its two children commented out -- the obvious edit for
    # "just use the defaults" -- is a present-but-null key.
    from homescreen.config import server_config
    resolved = server_config({"server": srv})
    # `isinstance(..., dict)` is unconditionally true and the port assertion
    # below it was swallowed by a bare `except ValueError: pass`, so this
    # tested nothing at all. Either we resolve a usable port or we raise --
    # there is no third outcome, and silence is not one of them.
    assert set(resolved) <= {"host", "port"}
    if srv == {"port": "eighty"}:
        with pytest.raises(ValueError):
            int(resolved["port"])
    else:
        assert int(resolved.get("port", 8080)) == 8080
        assert resolved.get("host", "0.0.0.0") in ("0.0.0.0", "127.0.0.1")


SERVE_CONFIG = """
server: {host: 127.0.0.1, port: 8099}
devices:
  - {id: radar, kind: gc9a01_client, render: device, feed: adsb}
feeds:
  adsb: {source: api, endpoint: "https://example.invalid/api"}
"""


def test_serve_startup_does_not_swallow_a_systemexit_from_inside(tmp_path, monkeypatch):
    # Symmetry with the fetch daemon's equivalent: `except Exception` must not
    # become `except BaseException`, or a deliberate exit is relabelled
    # EX_CONFIG. Without this, that mutation survives the whole suite.
    (tmp_path / "config.yaml").write_text(SERVE_CONFIG)

    def boom(*a, **k):
        raise SystemExit(3)

    monkeypatch.setattr("homescreen.serve.create_app", boom)
    with pytest.raises(SystemExit) as exc:
        serve_startup(tmp_path)
    assert exc.value.code == 3


def test_serve_startup_resolves_host_and_port(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(SERVE_CONFIG)
    app, host, port = serve_startup(tmp_path)
    assert (host, port) == ("127.0.0.1", 8099)
    assert app.test_client().get("/api/display/radar/health").status_code == 200


@pytest.mark.parametrize("text", [
    "devices: [\n",                                   # malformed YAML
    "server: {port: eighty}\ndevices: []\n",          # unusable port
])
def test_serve_startup_exits_78_on_a_config_fault(tmp_path: Path, text):
    (tmp_path / "config.yaml").write_text(text)
    with pytest.raises(SystemExit) as exc:
        serve_startup(tmp_path)
    assert exc.value.code == 78


@pytest.mark.parametrize("text", [
    "server:\ndevices: []\n",                         # present-but-null
    "server: '0.0.0.0:8080'\ndevices: []\n",          # wrong type, coerced
])
def test_serve_startup_falls_back_to_defaults(tmp_path: Path, text):
    (tmp_path / "config.yaml").write_text(text)
    _, host, port = serve_startup(tmp_path)
    assert (host, port) == ("0.0.0.0", 8080)


def test_poll_seconds_header_never_serves_the_string_none(tmp_path):
    # A dangling `poll_seconds:` is a present-but-null key; `.get(k, 5)` does
    # not fall back for it, and X-Poll-Seconds is config's projection.
    cfg = {"feeds": {"adsb": {"source": "api"}},
           "devices": [{"id": "radar", "render": "device", "feed": "adsb",
                        "poll_seconds": None}]}
    r = create_app(cfg, tmp_path).test_client().get("/api/display/radar/data")
    assert r.headers["X-Poll-Seconds"] == "5"


def test_source_label_survives_a_non_string_config(tmp_path):
    # .get() on an unhashable would raise TypeError and 500 every request.
    cfg = {"feeds": {"adsb": {"source": ["api"]}},
           "devices": [{"id": "radar", "render": "device", "feed": "adsb"}]}
    r = create_app(cfg, tmp_path).test_client().get("/api/display/radar/data")
    assert r.status_code == 200
    assert r.get_json()["feed"]["source"] == "unknown"


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
    assert c.get("/api/display/odd/health").status_code == 200, (
        "but /health is defined for every device")

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


def test_a_cache_file_carrying_infinity_is_rejected_whole(ctx):
    # Two guards stand between a bad feed and a device. This is the outer one:
    # cache.read refuses the envelope, so the device is told the feed is down
    # rather than handed a partial sky it cannot distinguish from a quiet one.
    import json as _json
    client, path, clock = ctx
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps({
        "fetched_at": "2026-01-01T00:00:00+00:00", "ok": True,
        "data": {"aircraft": [{"cs": "BAD", "age": float("inf")}]}},
        allow_nan=True))
    body = client.get("/api/display/radar/data").get_json()
    assert body["aircraft"] == []
    assert body["feed"]["ok"] is False


def test_a_non_finite_age_that_reaches_serve_is_dropped_per_aircraft(ctx):
    # And this is the inner one, unreachable through the file because of the
    # guard above -- so exercise it directly. Deleting the isfinite branch in
    # _servable otherwise changes no test.
    from homescreen.serve import _servable
    env = {"data": {"aircraft": [{"cs": "BAD", "age": float("nan")},
                                 {"cs": "INF", "age": float("inf")},
                                 {"cs": "TXT", "age": "soon"},
                                 {"cs": "OK", "age": 1.0}]}}
    out = _servable(env, dwell=2.0)
    assert [a["cs"] for a in out] == ["OK"], "an age we cannot trust is dropped"
    assert out[0]["age"] == 3.0, "dwell is added at serve time, not fetch time"


def test_a_negative_feed_age_is_never_reported(ctx):
    client, path, clock = ctx
    _seed(path)
    clock.t -= 3600
    assert client.get("/api/display/radar/data").get_json()["feed"]["age_s"] >= 0


def test_the_staleness_horizon_is_the_firmware_constant(ctx):
    from homescreen.serve import STALE_HORIZON_S
    assert STALE_HORIZON_S == 12.0, "kExtrapolationHorizonSec in adsb_client.h"


# --- a 304 must not freeze the ages it declined to send -----------------------

def _write_feed(path, now, aircraft):
    from datetime import datetime, timezone
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "fetched_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "ok": True, "error": None, "data": {"aircraft": aircraft}}))


def test_a_stalled_feed_stops_304ing_before_the_ages_go_stale(ctx):
    # VALIDATION F4 through the one door it did not cover. A body-less 304
    # leaves the device holding the ages from the last body it received; a
    # device that 304'd for 11.9s still believed its fix was 1.0s old. The fix
    # was 12.9s old -- ~3 km at 250 m/s -- and because pos_age_s stayed frozen
    # the firmware's own dimming test never fired either.
    client, path, clock = ctx
    _write_feed(path, clock.t, [{"cs": "IBE1", "age": 1.0}])
    etag = client.get("/api/display/radar/data").headers["ETag"]
    seen = []
    for _ in range(5):
        clock.t += 2.5                      # the fetcher is dead; nothing rewrites
        r = client.get("/api/display/radar/data", headers={"If-None-Match": etag})
        etag = r.headers["ETag"]
        if r.status_code == 200:
            seen.append(r.get_json()["aircraft"][0]["age"])
    assert seen == sorted(seen) and len(seen) >= 5, \
        "every poll during a stall must carry a fresh, growing age"
    assert seen[-1] >= 12.0, "the device must see its fix cross the horizon"


def test_a_healthy_feed_still_304s_for_free(ctx):
    # The bound must not cost the normal case. While the fetcher is alive the
    # dwell resets on every write and never leaves the first bucket, so an
    # unchanged sky is still answered with an empty 304.
    client, path, clock = ctx
    _write_feed(path, clock.t, [{"cs": "IBE1", "age": 1.0}])
    etag = client.get("/api/display/radar/data").headers["ETag"]
    for _ in range(4):
        clock.t += 5.0
        _write_feed(path, clock.t, [{"cs": "IBE1", "age": 1.0}])   # a real fetch
        r = client.get("/api/display/radar/data", headers={"If-None-Match": etag})
        etag = r.headers["ETag"]
        assert r.status_code == 304, "an unchanged sky costs a device nothing"


def test_the_hidden_age_error_is_bounded_by_one_bucket(ctx):
    from homescreen.serve import AGE_BUCKET_S, STALE_HORIZON_S
    assert AGE_BUCKET_S == 2.0
    assert AGE_BUCKET_S < STALE_HORIZON_S / 4, \
        "a 304 must not be able to hide a meaningful slice of the horizon"


def test_the_horizon_forces_a_body_even_inside_one_age_bucket(ctx):
    # Two guards keep a stalling feed honest and they overlap almost
    # everywhere, so `fresh = ok and dwell < STALE_HORIZON_S` -> `fresh = ok`
    # changed no test. The gap: two polls PAST the horizon that land in the
    # same 2s bucket have an identical ETag, so only the horizon check is left
    # to force a body -- and that body is what carries the true age.
    client, path, clock = ctx
    _write_feed(path, clock.t, [{"cs": "IBE1", "age": 1.0}])
    clock.t += 13.0                       # past 12s, bucket 6
    first = client.get("/api/display/radar/data")
    clock.t += 0.5                        # still bucket 6
    again = client.get("/api/display/radar/data",
                       headers={"If-None-Match": first.headers["ETag"]})
    assert again.headers["ETag"] == first.headers["ETag"], "same bucket"
    assert again.status_code == 200, "a feed past the horizon must not 304"
    assert again.get_json()["aircraft"][0]["age"] == pytest.approx(14.5)


def test_the_in_memory_telemetry_map_is_bounded(ctx):
    # /data stores query args in a process-lifetime dict and echoes them on
    # /api/status and /home. It stored 60,000-byte values before the registry's
    # own caps were applied here, and removing them changed no test.
    client, path, clock = ctx
    _seed(path)
    client.get("/api/display/radar/data?" + "&".join(
        f"k{i}=v{i}" for i in range(80)) + "&big=" + "A" * 5000)
    body = client.get("/api/status").get_json()
    tel = body["devices"][0]["last_telemetry"]
    assert len(tel) <= registry_mod.MAX_TELEMETRY_KEYS + 1   # +1 for "at"
    assert all(len(str(v)) <= registry_mod.MAX_VALUE_LEN
               for k, v in tel.items() if k != "at")
