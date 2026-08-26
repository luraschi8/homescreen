# tests/test_frame_api.py
import pytest

from homescreen import registry, render
from homescreen.serve import create_app

HW = "aabb00112233"
CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid"},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}
EPAPER_Q = "?fw=0.2.0&w=800&h=480&depth=1&layouts=fill"


@pytest.fixture
def client(tmp_path):
    return create_app(CFG, tmp_path, version="t").test_client()


@pytest.fixture(autouse=True)
def _needs_chromium():
    if render.find_chromium() is None:
        pytest.skip("no chromium/chrome on this machine")


def test_an_unassigned_device_gets_a_real_frame_not_an_error(client):
    # Spec §6.1: a newly flashed board must be able to tell you its id, not
    # sit blank or 404.
    r = client.get(f"/api/device/{HW}/frame{EPAPER_Q}")
    assert r.status_code == 200
    assert r.headers["X-Scene"] == "unassigned"
    assert len(r.get_data()) == 800 * 480 // 8


def test_the_frame_is_exactly_the_declared_geometry(client, tmp_path):
    for w, h in ((800, 480), (240, 240), (400, 300)):
        r = client.get(f"/api/device/{HW}/frame?w={w}&h={h}&depth=1")
        assert len(r.get_data()) == w * h // 8, f"{w}x{h}"


def test_an_assigned_scene_is_the_one_rendered(client, tmp_path):
    client.get(f"/api/device/{HW}/frame{EPAPER_Q}")
    client.patch(f"/api/devices/{HW}", json={"name": "desk", "scene": "clock"})
    r = client.get(f"/api/device/{HW}/frame{EPAPER_Q}")
    assert r.headers["X-Scene"] == "clock"


def test_an_unchanged_frame_is_a_304(client):
    first = client.get(f"/api/device/{HW}/frame{EPAPER_Q}")
    etag = first.headers["ETag"]
    again = client.get(f"/api/device/{HW}/frame{EPAPER_Q}",
                       headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.get_data() == b"", "a 304 carries no body"
    assert again.headers["X-Poll-Seconds"], "cadence still reaches the device"


def test_a_changed_scene_changes_the_etag(client):
    first = client.get(f"/api/device/{HW}/frame{EPAPER_Q}").headers["ETag"]
    client.patch(f"/api/devices/{HW}", json={"name": "desk", "scene": "clock"})
    assert client.get(f"/api/device/{HW}/frame{EPAPER_Q}").headers["ETag"] != first


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
    r = client.get(f"/api/device/{HW}/frame?w={w}&h={h}&depth=1")
    assert r.status_code == 400, why


@pytest.mark.parametrize("w", [0, -8, "abc", 99999999999])
def test_an_out_of_range_dimension_falls_back_rather_than_failing(client, w):
    # clean_caps drops nonsense before the geometry check ever sees it, so the
    # device gets the default panel size rather than an error it cannot act on.
    r = client.get(f"/api/device/{HW}/frame?w={w}&h=480&depth=1")
    assert r.status_code == 200
    assert len(r.get_data()) == 800 * 480 // 8


def test_the_frame_is_rendered_at_the_geometry_of_this_request(client):
    # Not the stored one: registration is unauthenticated, so trusting stored
    # caps lets any LAN host re-geometry someone else's panel, after which it
    # gets a wrong-length body at HTTP 200 and streams it straight at hardware.
    client.get(f"/api/device/{HW}/frame?w=800&h=480&depth=1")
    client.get(f"/api/device/{HW}/scene?w=1024&h=256")      # the poisoning call
    r = client.get(f"/api/device/{HW}/frame?w=800&h=480&depth=1")
    assert len(r.get_data()) == 800 * 480 // 8, "the panel gets what it asked for"


def test_a_render_failure_is_503_not_a_short_frame(client, monkeypatch):
    def boom(*a, **k):
        raise render.RenderError("chromium exploded")

    monkeypatch.setattr("homescreen.serve.render_frame", boom)
    r = client.get(f"/api/device/{HW}/frame{EPAPER_Q}")
    assert r.status_code == 503, "a device must not mistake an error for pixels"
    assert "render failed" in r.get_json()["error"]


def test_the_frame_length_header_matches_the_body(client):
    r = client.get(f"/api/device/{HW}/frame{EPAPER_Q}")
    assert int(r.headers["X-Frame-Bytes"]) == len(r.get_data())


def test_the_frame_decodes_back_to_a_readable_image(client, tmp_path):
    # 1 = black on the wire. If this comes out inverted the server is wrong,
    # and a real panel would show a photographic negative.
    from PIL import Image
    client.patch(f"/api/devices/{HW}", json={"name": "desk", "scene": "clock"}) \
        if client.get(f"/api/device/{HW}/frame{EPAPER_Q}") else None
    packed = client.get(f"/api/device/{HW}/frame{EPAPER_Q}").get_data()
    img = Image.frombytes("1", (800, 480), bytes(b ^ 0xFF for b in packed))
    hist = img.convert("L").histogram()
    assert hist[255] > hist[0], "a mostly-white page, not a negative"
    assert hist[0] > 0, "and there is actual ink on it"


def test_a_data_push_only_scene_returns_409_not_a_blank_frame(client, monkeypatch):
    from homescreen import scenes
    real = scenes._registry()
    monkeypatch.setattr("homescreen.scenes._registry",
                        lambda: {**real,
                                 "clock": lambda c: scenes.Scene(components=({"c": "x"},))})
    client.get(f"/api/device/{HW}/frame{EPAPER_Q}")
    client.patch(f"/api/devices/{HW}", json={"name": "d", "scene": "clock"})
    r = client.get(f"/api/device/{HW}/frame{EPAPER_Q}")
    assert r.status_code == 409


def test_the_scene_endpoint_carries_components_for_data_push(client):
    client.get("/api/device/ccdd44556677/scene?w=240&h=240&components=radar")
    client.patch("/api/devices/ccdd44556677", json={"name": "r", "scene": "planes"})
    body = client.get("/api/device/ccdd44556677/scene?w=240&h=240").get_json()
    assert body["layout"] == "fill"
    assert body["components"][0]["c"] == "radar"


def test_a_registry_write_failure_is_503_not_500(client, monkeypatch):
    # The registry lives on a microSD; a write failure must not take the
    # device's screen down with a 500 it cannot interpret.
    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr("homescreen.registry.touch", boom)
    assert client.get(f"/api/device/{HW}/scene").status_code == 503
    assert client.get(f"/api/device/{HW}/frame{EPAPER_Q}").status_code == 503
