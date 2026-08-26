# tests/test_mockdevice.py
"""The mock device is a test client: its job is to catch the server breaking
the device contract. These tests check that it actually would."""
import json
import re
from pathlib import Path

import io
import pytest

from homescreen import mockdevice, render
from homescreen.mockdevice import KINDS, MockDevice


class FakeResp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self._body = status, headers, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def dev():
    return MockDevice("http://pi:8080", "aabb00112233", "epaper")


def test_capabilities_are_declared_on_every_call(dev):
    url = dev._url("/api/device/x/scene")
    for expected in ("w=800", "h=480", "depth=1", "layouts=fill", "fw=mock-0.1"):
        assert expected in url, f"{expected} missing from {url}"


def test_telemetry_rides_along_so_the_fleet_view_is_populated(dev):
    url = dev._url("/api/device/x/scene")
    assert "rssi=" in url and "uptime=" in url and "errors=0" in url


def test_a_round_device_declares_different_capabilities():
    r = MockDevice("http://pi:8080", "cc", "round")
    url = r._url("/x")
    assert "w=240" in url and "depth=16" in url and "radar" in url


def test_a_short_frame_is_caught_as_a_contract_violation(dev, monkeypatch):
    # This is the whole point of the mock: a device streams the body straight
    # at the panel, so a short frame is a corrupt screen it cannot detect.
    monkeypatch.setattr(dev, "_get",
                        lambda p, conditional=False: (200, {"ETag": '"x"'}, b"\x00" * 100))
    with pytest.raises(SystemExit, match="CONTRACT VIOLATION"):
        dev.frame()


def test_a_correctly_sized_frame_is_accepted(dev, monkeypatch):
    good = b"\x00" * (800 * 480 // 8)
    monkeypatch.setattr(dev, "_get",
                        lambda p, conditional=False: (200, {"ETag": '"x"'}, good))
    status, _, body = dev.frame()
    assert status == 200 and len(body) == len(good)
    assert dev.etag == '"x"', "the etag is remembered for the next poll"


def test_a_304_is_not_length_checked(dev, monkeypatch):
    # Asserting the stub's own return tuple tested the stub. The real
    # postcondition is that a 304 leaves the remembered etag alone -- clearing
    # it would make the next poll unconditional and refetch every frame.
    dev.etag = '"kept"'
    monkeypatch.setattr(dev, "_get", lambda p, conditional=False: (304, {}, b""))
    status, _, body = dev.frame()
    assert status == 304 and body == b""
    assert dev.etag == '"kept"', "a 304 must not disturb the cached identity"


def test_the_conditional_header_is_sent_once_an_etag_is_known(dev, monkeypatch):
    seen = {}

    class Req:
        def __init__(self, url):
            self.url, self.headers = url, {}

        def add_header(self, k, v):
            seen[k] = v

    monkeypatch.setattr("homescreen.mockdevice.urllib.request.Request", Req)
    monkeypatch.setattr("homescreen.mockdevice.urllib.request.urlopen",
                        lambda r, timeout=0: FakeResp(304, {}, b""))
    dev.etag = '"abc"'
    dev._get("/x", conditional=True)
    assert seen.get("If-None-Match") == '"abc"'


def test_an_unreachable_server_exits_with_a_clear_message(dev, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr("homescreen.mockdevice.urllib.request.urlopen", boom)
    with pytest.raises(SystemExit, match="cannot reach"):
        dev._get("/x")


def test_a_non_200_scene_exits_rather_than_pretending(dev, monkeypatch):
    monkeypatch.setattr(dev, "_get",
                        lambda p, conditional=False: (500, {}, b'{"error":"boom"}'))
    with pytest.raises(SystemExit, match="HTTP 500"):
        dev.scene()


def test_error_count_climbs_and_is_reported_back(dev, monkeypatch):
    class HTTPErr(Exception):
        code = 503
        headers = {}

        def read(self):
            return b"{}"

    import urllib.error
    monkeypatch.setattr("homescreen.mockdevice.urllib.request.urlopen",
                        lambda r, timeout=0: (_ for _ in ()).throw(
                            urllib.error.HTTPError("u", 503, "x", {}, None)))
    dev._get("/x")
    assert dev.errors == 1
    assert "errors=1" in dev._url("/x"), "the server sees the device struggling"


def test_the_frame_decodes_to_the_declared_geometry(dev, tmp_path):
    packed = bytes([0xFF] * (800 * 480 // 8))
    out = tmp_path / "s.png"
    dev.to_png(packed, str(out))
    from PIL import Image
    img = Image.open(out)
    assert img.size == (800, 480)
    # 1 = black on the wire, so all-ones must decode to an all-black image.
    assert img.convert("L").histogram()[0] == 800 * 480


def test_every_declared_kind_has_a_byte_aligned_geometry():
    for name, caps in KINDS.items():
        assert (caps["w"] * caps["h"]) % 8 == 0, name


# --- main(), the half that was never executed ---------------------------------
# mockdevice exists to be the executable contract check between this server and
# a real device. Its whole poll loop -- the round-vs-epaper branch, 304
# handling, --out, the cycle counter -- had no coverage at all, so the tool that
# proves the contract was itself unproven.

@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Route the mock device's urllib calls into a real Flask app."""
    from homescreen.serve import create_app
    cfg = {"location": {"name": "Madrid", "timezone": "Europe/Madrid"},
           "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
           "devices": []}
    client = create_app(cfg, tmp_path, version="t").test_client()

    class FakeResp:
        def __init__(self, r):
            self.status, self.headers, self._b = r.status_code, dict(r.headers), r.get_data()

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        path = req.full_url.split("8080", 1)[-1] if "8080" in req.full_url \
            else req.full_url.split("://", 1)[-1].split("/", 1)[-1]
        if not path.startswith("/"):
            path = "/" + path
        r = client.get(path, headers=dict(req.header_items()))
        if r.status_code >= 300:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, r.status_code, "",
                                         r.headers, io.BytesIO(r.get_data()))
        return FakeResp(r)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(mockdevice.time, "sleep", lambda s: None)
    return client, tmp_path


def test_main_runs_a_full_epaper_cycle(wired, capsys, tmp_path):
    client, _ = wired
    if render.find_chromium() is None:
        pytest.skip("no chromium/chrome on this machine")
    out = tmp_path / "frame.png"
    rc = mockdevice.main(["--server", "http://127.0.0.1:8080", "--hw", "e1",
                          "--kind", "epaper", "--once", "--out", str(out)])
    printed = capsys.readouterr().out
    assert rc == 0
    assert "scene=unassigned assigned=False" in printed
    assert "48,000B" in printed, "an 800x480 1-bit panel is 48,000 bytes"
    # `"ink=" in printed` passed with a hardcoded 0%. The number is the whole
    # point: it is how this tool tells a rendered panel from a blank one and
    # from the full-black refresh that is worst case for e-paper ghosting.
    ink = float(re.search(r"ink=([\d.]+)%", printed).group(1))
    assert 0.05 < ink < 20.0, f"a text panel that is {ink}% ink is not a text panel"
    assert out.exists(), "--out must actually write the decoded frame"


def test_main_reports_components_for_a_round_device(wired, capsys):
    client, _ = wired
    client.get("/api/device/r1/scene?w=240&h=240&depth=16&components=radar")
    client.patch("/api/devices/r1", json={"name": "r1", "scene": "planes"})
    rc = mockdevice.main(["--hw", "r1", "--kind", "round", "--once"])
    printed = capsys.readouterr().out
    assert rc == 0 and "component radar" in printed


def test_main_says_so_when_a_scene_is_pixel_push_only(wired, capsys):
    client, _ = wired
    client.get("/api/device/r2/scene?w=240&h=240&depth=16&components=radar")
    client.patch("/api/devices/r2", json={"name": "r2", "scene": "clock"})
    mockdevice.main(["--hw", "r2", "--kind", "round", "--once"])
    assert "no components" in capsys.readouterr().out


def test_main_runs_the_requested_number_of_cycles(wired, capsys):
    if render.find_chromium() is None:
        pytest.skip("no chromium/chrome on this machine")
    mockdevice.main(["--hw", "e2", "--kind", "epaper", "--cycles", "3"])
    printed = capsys.readouterr().out
    assert "[1]" in printed and "[3]" in printed and "[4]" not in printed


def test_main_leaves_the_panel_alone_on_a_304(wired, capsys):
    if render.find_chromium() is None:
        pytest.skip("no chromium/chrome on this machine")
    mockdevice.main(["--hw", "e3", "--kind", "epaper", "--cycles", "2"])
    assert "frame unchanged (304)" in capsys.readouterr().out, \
        "the second cycle must reuse the etag from the first"


def test_main_reports_an_http_error_without_pretending_it_is_a_frame(
        wired, capsys, monkeypatch):
    # The else-branch: anything that is not 200 or 304 must be printed as the
    # failure it is. Printing a byte count for an error body would make the
    # contract check report success on a broken server.
    def boom(*a, **k):
        raise render.RenderError("chromium exploded")

    monkeypatch.setattr("homescreen.serve.render_frame", boom)
    mockdevice.main(["--hw", "e4", "--kind", "epaper", "--once"])
    printed = capsys.readouterr().out
    assert "frame -> HTTP 503" in printed
    assert "render failed" in printed
    assert "B scene=" not in printed, "an error body is not a framebuffer"
