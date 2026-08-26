# tests/test_home.py
import json
import subprocess
from pathlib import Path

import pytest

from homescreen.serve import create_app, resolve_version

CFG = {
    "server": {"host": "0.0.0.0", "port": 8080},
    "feeds": {"adsb": {"source": "api", "endpoint": "https://example.invalid/api",
                       "fetch_seconds": 3}},
    "devices": [
        {"id": "radar", "kind": "gc9a01_client", "render": "device", "feed": "adsb",
         "home": {"lat": 40.4168, "lon": -3.7038}, "radius_km": 60,
         "max_aircraft": 20, "poll_seconds": 5},
        {"id": "kitchen", "kind": "epaper_client", "render": "server", "feed": "adsb",
         "poll_seconds": 60},
    ],
}


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    clock = Clock()
    monkeypatch.setattr(
        "homescreen.cache._now_iso",
        lambda: datetime.fromtimestamp(clock.t, timezone.utc).isoformat())
    app = create_app(CFG, tmp_path, clock=clock, version="abc1234")
    return app.test_client(), tmp_path / "feed" / "radar.json", clock


def _seed(path, n=2):
    from homescreen.cache import write_cache
    write_cache(path, {"aircraft": [
        {"lat": 40.5, "lon": -3.6, "nose": 90.0, "trk": 90.0, "gs": 400.0,
         "ve": 0.2, "vn": 0.0, "age": 2.0, "dst": 7.4 + i,
         "cs": f"IBE{i}", "ty": "A320", "alt": "3675 ft"} for i in range(n)]})


@pytest.mark.parametrize("route", ["/", "/home"])
def test_home_renders_for_a_human(ctx, route):
    client, path, _ = ctx
    _seed(path)
    r = client.get(route)
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    body = r.get_data(as_text=True)
    # The four things the page exists to answer.
    assert "abc1234" in body, "which code is running"
    assert "radar" in body and "kitchen" in body, "which devices are registered"
    assert "/api/display/radar/data" in body, "where the data is served"
    assert "adsb.fi" in body, "where the data comes from"


def test_home_shows_live_feed_state(ctx):
    client, path, clock = ctx
    _seed(path, n=3)
    clock.t += 4.0
    body = client.get("/home").get_data(as_text=True)
    assert "3" in body, "aircraft count"
    assert "4.0" in body or "4 s" in body, "feed age"


def test_home_works_before_any_fetch_has_happened(ctx):
    # A cold Pi with no cache must still render, not 500.
    client, _, _ = ctx
    r = client.get("/home")
    assert r.status_code == 200
    assert "never" in r.get_data(as_text=True).lower()


def test_home_never_500s_on_a_corrupt_cache(ctx):
    client, path, _ = ctx
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"fetched_at":"garbage","ok":true,"data":{"aircraft":5}}')
    assert client.get("/home").status_code == 200


def test_home_does_not_leak_secrets(ctx):
    # config.local.yaml holds API keys (SPEC 7.4). The status page is
    # unauthenticated on the LAN, so it must render config STRUCTURE only.
    cfg = json.loads(json.dumps(CFG))
    cfg["feeds"]["adsb"]["api_key"] = "s3cret-key-value"
    cfg["quotes"] = {"api_key": "another-s3cret"}
    client = create_app(cfg, Path("/tmp"), version="v").test_client()
    body = client.get("/home").get_data(as_text=True)
    assert "s3cret" not in body
    assert "another-s3cret" not in body


def test_status_json_mirrors_the_page(ctx):
    client, path, clock = ctx
    _seed(path)
    clock.t += 2.0
    body = client.get("/api/status").get_json()
    assert body["version"] == "abc1234"
    assert body["uptime_s"] == pytest.approx(2.0, abs=0.01)
    ids = [d["id"] for d in body["devices"]]
    assert ids == ["radar", "kitchen"]
    radar = body["devices"][0]
    assert radar["feed"]["ok"] is True
    assert radar["feed"]["aircraft"] == 2
    assert radar["endpoints"]["data"] == "/api/display/radar/data"
    # A pixel-push device has no data endpoint yet (Phase C).
    assert body["devices"][1]["endpoints"]["data"] is None


def test_status_json_does_not_leak_secrets():
    cfg = json.loads(json.dumps(CFG))
    cfg["feeds"]["adsb"]["api_key"] = "s3cret-key-value"
    client = create_app(cfg, Path("/tmp"), version="v").test_client()
    assert "s3cret" not in json.dumps(client.get("/api/status").get_json())


def test_resolve_version_reads_the_git_sha(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "x"], cwd=tmp_path, check=True)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=tmp_path,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert resolve_version(tmp_path) == sha


def test_resolve_version_degrades_when_git_is_absent(tmp_path):
    # The Pi clone is a git repo, but a tarball deploy or a missing git binary
    # must not stop the server starting.
    assert resolve_version(tmp_path / "not-a-repo") == "unknown"


def test_status_skips_device_entries_with_no_id(tmp_path):
    # Same guard as fetch_targets: an id-less entry must not reach the page,
    # where _device_summary would index dev["id"] and 500 the status route.
    cfg = {"feeds": {"adsb": {}},
           "devices": [{"kind": "gc9a01_client", "render": "device"},
                       {"id": "radar", "render": "device", "feed": "adsb"}]}
    client = create_app(cfg, tmp_path, version="v").test_client()
    assert [d["id"] for d in client.get("/api/status").get_json()["devices"]] == ["radar"]
    assert client.get("/home").status_code == 200


def test_resolve_version_treats_an_empty_sha_as_unknown(tmp_path, monkeypatch):
    # `git rev-parse` can exit 0 with empty stdout in odd repo states; an empty
    # version string would render as a blank field rather than an honest label.
    class R:
        returncode = 0
        stdout = "  \n"

    monkeypatch.setattr("homescreen.serve.subprocess.run", lambda *a, **k: R())
    assert resolve_version(tmp_path) == "unknown"


from homescreen import registry as _registry


def _reg_app(tmp_path, clock=None, cfg=None):
    return create_app(cfg if cfg is not None else {"feeds": {"adsb": {}}, "devices": []},
                      tmp_path, clock=clock or Clock(), version="abc1234")


def test_home_lists_registered_devices_with_state(tmp_path):
    clock = Clock()
    client = _reg_app(tmp_path, clock).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", fw="0.2.0",
                    caps={"w": 240, "h": 240}, now=clock.t)
    _registry.assign(tmp_path, "a4cf12ab3c44", name="radar", scene="planes")
    _registry.touch(tmp_path, "deadbeef0000", fw="0.1.0", now=clock.t)
    body = client.get("/home").get_data(as_text=True)
    assert "radar" in body and "planes" in body
    assert "a4cf12ab3c44" in body, "hw id shown so a new board is identifiable"
    assert "unassigned" in body and "unnamed" in body
    assert "0.2.0" in body and "0.1.0" in body
    assert "240x240" in body


def test_home_marks_a_silent_device_offline(tmp_path):
    clock = Clock()
    client = _reg_app(tmp_path, clock).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=clock.t)
    assert "offline" not in client.get("/home").get_data(as_text=True).lower()
    clock.t += 3600
    assert "offline" in client.get("/home").get_data(as_text=True).lower()


def test_home_header_counts_the_fleet_not_the_config(tmp_path):
    clock = Clock()
    client = _reg_app(tmp_path, clock).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=clock.t)
    _registry.touch(tmp_path, "deadbeef0000", now=clock.t)
    body = client.get("/home").get_data(as_text=True)
    assert "2 device(s) registered" in body
    assert "2 online" in body


def test_home_renders_with_an_empty_fleet(tmp_path):
    client = _reg_app(tmp_path).test_client()
    body = client.get("/home").get_data(as_text=True)
    assert client.get("/home").status_code == 200
    assert "no devices have called in yet" in body


def test_status_json_carries_the_fleet(tmp_path):
    client = _reg_app(tmp_path).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", fw="0.2.0", now=1_700_000_000.0)
    fleet = client.get("/api/status").get_json()["fleet"]
    assert fleet[0]["hw"] == "a4cf12ab3c44"
    assert fleet[0]["scene"] == "unassigned"


def test_the_fleet_view_does_not_leak_telemetry_into_html(tmp_path):
    # telemetry is device-supplied; it must be escaped, not injected.
    client = _reg_app(tmp_path).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=1_700_000_000.0,
                    telemetry={"x": "<script>alert(1)</script>"})
    body = client.get("/home").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_a_name_containing_a_slash_is_refused_outright(tmp_path):
    # The alias resolves names in a URL path, so a slash would misroute.
    client = _reg_app(tmp_path).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=1_700_000_000.0)
    r = client.patch("/api/devices/a4cf12ab3c44", json={"name": "<b>x</b>"})
    assert r.status_code == 400


def test_a_device_named_with_html_is_escaped(tmp_path):
    # Slash-free, so it passes name validation and reaches the renderer -- the
    # escaping is what must stop it, not the name check.
    client = _reg_app(tmp_path).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=1_700_000_000.0)
    _registry.assign(tmp_path, "a4cf12ab3c44", name="<b>bold")
    body = client.get("/home").get_data(as_text=True)
    assert "<b>bold" not in body
    assert "&lt;b&gt;bold" in body
