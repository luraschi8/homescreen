# tests/test_frame_api.py
import pytest

from homescreen import registry, render
from homescreen.serve import create_app
from tests.conftest import FrozenClock

HW = "aabb00112233"
CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}
EPAPER_Q = "?fw=0.2.0&w=800&h=480&depth=1&layouts=fill"


@pytest.fixture
def client(tmp_path):
    return create_app(CFG, tmp_path, version="t", clock=FrozenClock()).test_client()


@pytest.fixture
def needs_chromium(monkeypatch):
    """A frame, by whatever means. Opt-in, NOT autouse.

    Most of this file asserts on lengths, ETags, status codes and headers --
    none of which need a real browser, only a real framebuffer. Skipping them
    meant the deploy target, which has no Chromium (CLAUDE.md §2), reported
    green with the entire frame route untested. So we stub when there is no
    browser and skip nothing.

    Tests whose subject IS the browser's output -- polarity, ink coverage,
    antialiasing -- use `real_chromium` instead and genuinely skip.
    """
    if render.find_chromium() is not None:
        return
    from PIL import Image

    def stub(html, w, h, out_png, binary=None):
        # The ink pattern is derived from the HTML, because a browser's
        # defining property here is that different content makes different
        # pixels -- a fixed pattern would make every scene share an ETag and
        # quietly disarm the tests that check a scene change reaches the glass.
        import hashlib
        seed = hashlib.sha256(html.encode()).digest()
        img = Image.new("1", (w, h), 1)
        for i, byte in enumerate(seed[:32]):
            for bit in range(8):
                if byte >> bit & 1:
                    x, y = (i * 8 + bit) % w, (i * 3) % h
                    img.putpixel((x, y), 0)
        img.save(out_png)

    monkeypatch.setattr("homescreen.render.html_to_png", stub)


@pytest.fixture
def real_chromium():
    """For the handful of tests that check what the BROWSER produced."""
    if render.find_chromium() is None:
        pytest.skip("no chromium/chrome on this machine")


def test_an_unassigned_device_gets_a_real_frame_not_an_error(client, needs_chromium, tmp_path):
    # Spec §6.1: a newly flashed board must be able to tell you its id, not
    # sit blank or 404.
    client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")     # first contact registers it
    registry.set_approval(tmp_path, HW, True)
    r = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    assert r.status_code == 200
    assert r.headers["X-Scene"] == "unassigned"
    assert len(r.get_data()) == 800 * 480 // 8


def test_the_frame_is_exactly_the_declared_geometry(client, needs_chromium, tmp_path):
    # One hw per geometry: a single device asking for three different panels
    # inside one second is not a device, and the cold-render throttle says so.
    for i, (w, h) in enumerate(((800, 480), (240, 240), (400, 300))):
        client.get(f"/api/devices/geo{i}/scene?w={w}&h={h}&depth=1")
        r = client.get(f"/api/devices/geo{i}/frame?w={w}&h={h}")
        assert len(r.get_data()) == w * h // 8, f"{w}x{h}"


def test_an_assigned_scene_is_the_one_rendered(client, needs_chromium, tmp_path):
    client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    registry.set_approval(tmp_path, HW, True)
    client.patch(f"/api/devices/{HW}", json={"name": "desk", "scene": "clock"})
    r = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    assert r.headers["X-Scene"] == "clock"


def test_an_unchanged_frame_is_a_304(client, needs_chromium):
    first = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    etag = first.headers["ETag"]
    again = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}",
                       headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.get_data() == b"", "a 304 carries no body"
    assert again.headers["X-Poll-Seconds"], "cadence still reaches the device"


def test_a_changed_scene_changes_the_etag(client, needs_chromium):
    first = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}").headers["ETag"]
    client.patch(f"/api/devices/{HW}", json={"name": "desk", "scene": "clock"})
    assert client.get(f"/api/devices/{HW}/frame{EPAPER_Q}").headers["ETag"] != first


@pytest.mark.parametrize("w,h,why", [
    (101, 101, "odd width"),
    (12, 8, "(w*h)%8 is 0 but w%8 is 4 -- rows pad, so this truncates"),
    (4, 2, "same trap, smaller"),
    (4096, 4096, "a 2 MB frame from one unauthenticated GET"),
])
def test_an_unrenderable_geometry_is_refused_before_any_work(client, w, h, why):
    # Rejected with a 400 rather than forking a browser for ~3s and then
    # failing to pack -- which returned a retryable 503 that was never cached,
    # so one misconfigured device could occupy both render slots forever.
    r = client.get(f"/api/devices/{HW}/frame?w={w}&h={h}&depth=1")
    assert r.status_code == 400, why


@pytest.mark.parametrize("w", [0, -8, "abc", 99999999999])
def test_an_unreadable_dimension_is_refused_not_silently_replaced(client,
                                                                  needs_chromium, w):
    # This used to fall back to 800x480. Falling back is wrong here even when
    # the input is junk: a device that asked for something we could not read
    # would receive 48,000 bytes with no indication they were not what it asked
    # for, and stream them at whatever panel it actually has.
    r = client.get(f"/api/devices/{HW}/frame?w={w}&h=480&depth=1")
    assert r.status_code == 400
    assert r.mimetype == "application/json", "an error is never a framebuffer"


def test_an_operator_scene_change_is_not_throttled(client, needs_chromium,
                                                   cold_frame_cache, tmp_path):
    # The cold-render throttle protects the render queue from strangers, not
    # from the operator. A scene change must reach the glass on the next poll,
    # not half a poll interval later -- which on an e-paper is 15 seconds.
    client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    registry.set_approval(tmp_path, HW, True)
    client.patch(f"/api/devices/{HW}", json={"name": "d", "scene": "clock"})
    r = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    assert r.status_code == 200 and r.headers["X-Scene"] == "clock"


def test_a_stranger_cannot_spend_the_render_queue(client, needs_chromium):
    # ~2.9s of Chromium per cold frame against 2 slots: 20 unauthenticated
    # connections took the panel off the air for as long as they kept it up.
    # A caller asking faster than the device polls is now answered instantly.
    client.get(f"/api/devices/flood/scene?w=800&h=480&depth=1")
    first = client.get("/api/devices/flood/frame?w=800&h=480")
    assert first.status_code == 200
    render.clear_cache()                       # force the expensive path
    r = client.get("/api/devices/flood/frame?w=800&h=480")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "5"


def test_a_cached_frame_is_never_throttled(client, needs_chromium):
    # The throttle must gate cost, not correctness: a device polling for a
    # frame we already hold gets it, however often it asks. Configured, so the
    # unconfigured-device budget is out of the picture and this tests one rule.
    client.get("/api/devices/warm/scene?w=800&h=480&depth=1")
    client.patch("/api/devices/warm", json={"name": "warm", "scene": "clock"})
    assert client.get("/api/devices/warm/frame?w=800&h=480").status_code == 200
    for _ in range(5):
        assert client.get("/api/devices/warm/frame?w=800&h=480").status_code == 200


def test_a_render_failure_is_503_not_a_short_frame(client, monkeypatch):
    def boom(*a, **k):
        raise render.RenderError("chromium exploded")

    monkeypatch.setattr("homescreen.serve.render_frame", boom)
    r = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    assert r.status_code == 503, "a device must not mistake an error for pixels"
    assert "render failed" in r.get_json()["error"]


def test_the_frame_length_header_matches_the_body(client, needs_chromium):
    r = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    assert int(r.headers["X-Frame-Bytes"]) == len(r.get_data())


def test_the_frame_decodes_back_to_a_readable_image(client, real_chromium,
                                                    tmp_path):
    # 1 = black on the wire. If this comes out inverted the server is wrong,
    # and a real panel would show a photographic negative.
    from PIL import Image
    client.patch(f"/api/devices/{HW}", json={"name": "desk", "scene": "clock"}) \
        if client.get(f"/api/devices/{HW}/frame{EPAPER_Q}") else None
    packed = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}").get_data()
    img = Image.frombytes("1", (800, 480), bytes(b ^ 0xFF for b in packed))
    hist = img.convert("L").histogram()
    assert hist[255] > hist[0], "a mostly-white page, not a negative"
    assert hist[0] > 0, "and there is actual ink on it"


def test_a_data_push_only_scene_returns_409_not_a_blank_frame(client, monkeypatch, tmp_path):
    from homescreen import scenes
    real = scenes._registry()
    monkeypatch.setattr("homescreen.scenes._registry",
                        lambda: {**real,
                                 "clock": lambda c: scenes.Scene(components=({"c": "x"},))})
    client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    registry.set_approval(tmp_path, HW, True)
    client.patch(f"/api/devices/{HW}", json={"name": "d", "scene": "clock"})
    r = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    assert r.status_code == 409


def test_the_scene_endpoint_carries_components_for_data_push(client, tmp_path):
    client.get("/api/devices/ccdd44556677/scene?w=240&h=240&components=radar")
    registry.set_approval(tmp_path, HW, True)
    registry.set_approval(tmp_path, "ccdd44556677", True)
    client.patch("/api/devices/ccdd44556677", json={"name": "r", "scene": "planes"})
    body = client.get("/api/devices/ccdd44556677/scene?w=240&h=240").get_json()
    assert body["layout"] == "fill"
    assert body["components"][0]["c"] == "radar"


def test_a_registry_write_failure_is_503_not_500(client, monkeypatch):
    # The registry lives on a microSD; a write failure must not take the
    # device's screen down with a 500 it cannot interpret.
    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr("homescreen.registry.touch", boom)
    assert client.get(f"/api/devices/{HW}/scene").status_code == 503
    assert client.get(f"/api/devices/{HW}/frame{EPAPER_Q}").status_code == 503


def test_the_frame_is_served_as_binary_not_json(client, needs_chromium):
    # A device streams the body straight at a panel; a JSON content type would
    # mean somebody had wrapped it.
    r = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    assert r.mimetype == "application/octet-stream"


def test_a_device_declaring_no_geometry_is_told_to_declare_one(client, needs_chromium):
    # There used to be an 800x480 fallback here. It was the hole: the fallback
    # read stored caps, and stored caps are written by whoever asks last.
    r = client.get(f"/api/devices/{HW}/frame?fw=0.1")
    assert r.status_code == 400
    assert "w=" in r.get_json()["error"] and "h=" in r.get_json()["error"]


def test_a_device_is_told_a_default_cadence_before_it_is_assigned(client):
    r = client.get(f"/api/devices/{HW}/scene")
    assert r.headers["X-Poll-Seconds"] == "5"


def test_components_the_device_did_not_declare_are_dropped_and_reported(client, tmp_path):
    # ADDENDUM §5.5: the device never receives something it cannot draw, and
    # the substitution is reported rather than silent.
    client.get("/api/devices/RR/scene?w=240&h=240&components=text")
    registry.set_approval(tmp_path, "RR", True)
    client.patch("/api/devices/RR", json={"name": "r", "scene": "planes"})
    body = client.get("/api/devices/RR/scene?w=240&h=240&components=text").get_json()
    assert body["components"] == []
    assert body["unsupported"] == ["radar"]


def test_a_device_that_declares_the_component_still_gets_it(client, tmp_path):
    client.get("/api/devices/RR2/scene?w=240&h=240&components=radar,text")
    registry.set_approval(tmp_path, "RR2", True)
    client.patch("/api/devices/RR2", json={"name": "r2", "scene": "planes"})
    body = client.get("/api/devices/RR2/scene?w=240&h=240&components=radar").get_json()
    assert [c["c"] for c in body["components"]] == ["radar"]
    assert "unsupported" not in body


def test_telemetry_does_not_swallow_capability_or_firmware_keys(client, tmp_path):
    client.get(f"/api/devices/{HW}/scene?w=240&h=240&depth=16&fw=1.2&rssi=-64")
    rec = registry.load(tmp_path)[HW]
    assert rec["telemetry"] == {"rssi": "-64"}
    assert rec["fw"] == "1.2"


def test_a_named_registry_device_is_reachable_by_its_friendly_name(client, tmp_path):
    # ADDENDUM §4.5. Without the alias the fleet view lists devices you cannot
    # curl -- resolve_name existed and was wired to no route.
    client.get("/api/devices/aa99bb88cc77/scene?w=240&h=240")
    registry.set_approval(tmp_path, "aa99bb88cc77", True)
    client.patch("/api/devices/aa99bb88cc77", json={"name": "kitchen",
                                                    "scene": "planes"})
    assert client.get("/api/display/kitchen/health").status_code == 200
    assert client.get("/api/display/nosuchname/health").status_code == 404


def test_a_config_device_still_wins_its_own_name(tmp_path):
    # The live deployment serves /api/display/radar/data from config.yaml.
    cfg = {**CFG, "devices": [{"id": "radar", "kind": "gc9a01_client",
                               "render": "device", "feed": "adsb",
                               "poll_seconds": 5}]}
    c = create_app(cfg, tmp_path, version="t", clock=FrozenClock()).test_client()
    assert c.get("/api/display/radar/data").status_code == 200


def test_a_busy_render_queue_is_a_503_with_a_retry_hint(client, monkeypatch,
                                                        cold_frame_cache):
    # RenderBusy was tested at the render layer only; the ROUTE's except clause
    # never executed, so deleting it fell through to the RenderError handler --
    # still a 503, but silently without Retry-After, which is the only thing
    # telling a device when to come back.
    def busy(*a, **k):
        raise render.RenderBusy("render queue busy for 20s")

    monkeypatch.setattr("homescreen.serve.render_frame", busy)
    r = client.get(f"/api/devices/{HW}/frame{EPAPER_Q}")
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "5"
    assert "busy" in r.get_json()["error"]


def test_the_frame_cache_drops_the_least_recently_used_entry(monkeypatch,
                                                             cold_frame_cache):
    # `popitem(last=False)` -> `last=True` survived: nothing distinguished
    # dropping the oldest from dropping the newest, so the cache could have
    # been evicting the frame it had just built.
    from PIL import Image
    monkeypatch.setattr(render, "html_to_png",
                        lambda html, w, h, out, binary=None:
                        Image.new("1", (w, h), 1).save(out))
    for i in range(render._CACHE_MAX):
        render.render_frame(f"<p>{i}</p>", 800, 480)
    render.render_frame("<p>0</p>", 800, 480)          # touch the oldest
    render.render_frame("<p>new</p>", 800, 480)        # forces one eviction
    assert render.is_cached("<p>0</p>", 800, 480), "the touched entry survives"
    assert not render.is_cached("<p>1</p>", 800, 480), "the next-oldest goes"
    assert render.is_cached("<p>new</p>", 800, 480)


def _compose_a_dashboard(client, tmp_path):
    """A device showing a clock and a masthead together -- a composed page."""
    client.get(f"/api/devices/{HW}/scene{EPAPER_Q}")
    registry.set_approval(tmp_path, HW, True)
    client.put(f"/api/devices/{HW}/schedule", json={
        "views": {"panel": {"template": "dashboard", "placements": [
            {"id": "m", "region": "masthead", "component": "date",
             "options": {}},
            {"id": "c", "region": "main_left", "component": "clock",
             "options": {}}]}},
        "schedule": {"tz": "Europe/Madrid", "default": "panel", "slots": []}})


def test_a_composed_page_polls_at_its_fastest_component(client, needs_chromium,
                                                        tmp_path):
    # The frame route kept the cadence of the LEGACY per-device scene, so a
    # panel carrying a clock -- which asks to be woken at the next minute
    # boundary -- was told to come back in ten minutes. That is the whole
    # ticking-clock premise of CLAUDE.md disabled by one assignment.
    _compose_a_dashboard(client, tmp_path)
    resp = client.get(f"/api/devices/{HW}/frame?w=800&h=480")
    assert resp.status_code == 200
    assert int(resp.headers["X-Poll-Seconds"]) <= 60, resp.headers


def test_a_composed_page_reports_the_view_it_is_showing(client, needs_chromium,
                                                        tmp_path):
    # X-Scene said "date" -- the legacy per-device field -- while the glass
    # held the whole dashboard.
    _compose_a_dashboard(client, tmp_path)
    resp = client.get(f"/api/devices/{HW}/frame?w=800&h=480")
    assert resp.headers["X-Scene"] == "panel"
