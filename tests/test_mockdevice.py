# tests/test_mockdevice.py
"""The mock device is a test client: its job is to catch the server breaking
the device contract. These tests check that it actually would."""
import json
from pathlib import Path

import pytest

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
    monkeypatch.setattr(dev, "_get", lambda p, conditional=False: (304, {}, b""))
    status, _, body = dev.frame()
    assert status == 304 and body == b""


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
