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
    return app.test_client(), _sky_path(tmp_path, CFG), clock


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
    # What the FLEET page exists to answer. Per-source endpoints and per-screen
    # detail moved to /settings and /device/<hw> respectively; the fleet view
    # answers "what is here and is it working", and links to the rest.
    assert "abc1234" in body, "which code is running"
    assert "adsb.fi" in body, "where the data comes from"
    assert 'href="/settings"' in body, "and how to get at it"


def test_the_settings_page_shows_the_fetches_the_fleet_implies(ctx):
    # Was: per-device feed health, with an aircraft count. Sources are JOBS
    # now -- one fetch serves every screen wanting the same sky -- so the
    # question this page answers changed from "how is this device's feed" to
    # "what is being fetched, for whom, and is it working".
    client, path, clock = ctx
    _seed(path, n=3)
    hw = "aabb00112233"
    q = "w=240&h=240&depth=16&shape=round&components=radar,draw_list"
    client.get(f"/api/devices/{hw}/scene?{q}")
    client.put(f"/api/devices/{hw}/membership", json={"approved": True})
    client.patch(f"/api/devices/{hw}", json={"name": "salon", "scene": "planes"})

    body = client.get("/settings").get_data(as_text=True)
    assert "adsb" in body, "the job is listed"
    assert "al día" in body, "and it has data"
    assert "salon" in body or hw in body, "and it says which screen wants it"


def test_home_works_before_any_fetch_has_happened(ctx):
    # A cold Pi with no cache must still render, not 500.
    client, _, _ = ctx
    assert client.get("/home").status_code == 200
    r = client.get("/settings")
    assert r.status_code == 200
    # Sources are jobs, and a fleet with nothing assigned implies no fetches.
    # Saying so beats an empty area that reads as a broken page.
    assert "Ninguna pantalla pide datos" in r.get_data(as_text=True)


def test_home_never_500s_on_a_corrupt_cache(ctx):
    client, path, _ = ctx
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"fetched_at":"garbage","ok":true,"data":{"aircraft":5}}')
    assert client.get("/home").status_code == 200


def test_home_does_not_leak_secrets(ctx, tmp_path):
    # config.local.yaml holds API keys (SPEC 7.4). The status page is
    # unauthenticated on the LAN, so it must render config STRUCTURE only.
    cfg = json.loads(json.dumps(CFG))
    cfg["feeds"]["adsb"]["api_key"] = "s3cret-key-value"
    cfg["quotes"] = {"api_key": "another-s3cret"}
    client = create_app(cfg, tmp_path, version="v").test_client()
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


def test_status_json_does_not_leak_secrets(tmp_path):
    cfg = json.loads(json.dumps(CFG))
    cfg["feeds"]["adsb"]["api_key"] = "s3cret-key-value"
    client = create_app(cfg, tmp_path, version="v").test_client()
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


def _sky_path(cache_dir, cfg, options=None):
    """Where the radar reads its sky, derived the way the scene derives it.

    Tests used to write `cache/feed/adsb.json` -- the per-device file from when
    there was one feed and one radar. The scene now declares a requirement and
    is handed a Reading, so a fixture that hardcodes a path is one that agrees
    with itself and with nothing else.
    """
    from homescreen import fetch, scenes
    need = scenes.needs("planes", options or {}, cfg)[0]
    key = fetch.providers.key(need["provider"],
                        fetch.providers.clean_params(need["provider"], need["params"]))
    path = fetch.store.path_for(cache_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path




def _reg_app(tmp_path, clock=None, cfg=None):
    return create_app(cfg if cfg is not None else {"feeds": {"adsb": {}}, "devices": []},
                      tmp_path, clock=clock or Clock(), version="abc1234")


def test_home_lists_registered_devices_with_state(tmp_path):
    clock = Clock()
    client = _reg_app(tmp_path, clock).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", fw="0.2.0",
                    caps={"w": 240, "h": 240}, now=clock.t)
    _registry.set_approval(tmp_path, "a4cf12ab3c44", True)
    _registry.assign(tmp_path, "a4cf12ab3c44", name="radar", scene="planes")
    _registry.touch(tmp_path, "deadbeef0000", fw="0.1.0", now=clock.t)
    _registry.set_approval(tmp_path, "deadbeef0000", True)
    body = client.get("/home").get_data(as_text=True)
    assert "radar" in body and "planes" in body
    assert "a4cf12ab3c44" in body, "hw id shown so a new board is identifiable"
    assert "sin asignar" in body and "sin nombre" in body
    # Firmware and geometry are facts about ONE screen and are on its page --
    # a fleet list that repeats them for eight panels is a wall of text.
    page = client.get("/device/a4cf12ab3c44").get_data(as_text=True)
    assert "0.2.0" in page
    assert "240\u00d7240" in page


def test_home_marks_a_silent_device_offline(tmp_path):
    clock = Clock()
    client = _reg_app(tmp_path, clock).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=clock.t)
    _registry.set_approval(tmp_path, "a4cf12ab3c44", True)
    assert "sin conexión" not in client.get("/home").get_data(as_text=True)
    clock.t += 3600
    assert "sin conexión" in client.get("/home").get_data(as_text=True)


def test_home_header_counts_the_fleet_not_the_config(tmp_path):
    clock = Clock()
    client = _reg_app(tmp_path, clock).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=clock.t)
    _registry.set_approval(tmp_path, "a4cf12ab3c44", True)
    _registry.touch(tmp_path, "deadbeef0000", now=clock.t)
    _registry.set_approval(tmp_path, "deadbeef0000", True)
    body = client.get("/home").get_data(as_text=True)
    assert "2 pantalla(s)" in body
    assert "2 en línea" in body


def test_home_renders_with_an_empty_fleet(tmp_path):
    client = _reg_app(tmp_path).test_client()
    body = client.get("/home").get_data(as_text=True)
    assert client.get("/home").status_code == 200
    assert "Ninguna pantalla" in body


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
    body = client.get("/device/a4cf12ab3c44").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_a_name_containing_a_slash_is_refused_outright(tmp_path):
    # The alias resolves names in a URL path, so a slash would misroute.
    client = _reg_app(tmp_path).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=1_700_000_000.0)
    _registry.set_approval(tmp_path, "a4cf12ab3c44", True)
    r = client.patch("/api/devices/a4cf12ab3c44", json={"name": "<b>x</b>"})
    assert r.status_code == 400


def test_a_device_named_with_html_is_escaped(tmp_path):
    # Slash-free, so it passes name validation and reaches the renderer -- the
    # escaping is what must stop it, not the name check.
    client = _reg_app(tmp_path).test_client()
    _registry.touch(tmp_path, "a4cf12ab3c44", now=1_700_000_000.0)
    _registry.set_approval(tmp_path, "a4cf12ab3c44", True)
    _registry.assign(tmp_path, "a4cf12ab3c44", name="<b>bold")
    body = client.get("/home").get_data(as_text=True)
    assert "<b>bold" not in body
    assert "&lt;b&gt;bold" in body


# --- a placeholder is not an offline panel -----------------------------------
#
# `config.yaml` can declare a device, and seeding gives it a synthetic
# `cfg:<id>` record. Until the real board turns up that record has never
# spoken: no capabilities, no firmware, `first_seen == last_seen`. The fleet
# showed it exactly like a panel that had gone offline, which is the opposite
# meaning -- one says "go and check the cable", the other says "this was never
# a panel".

def _home(client):
    return client.get("/home").get_data(as_text=True)


def _row(html, hw):
    """The fleet row for one screen. Scoped, because the fixture's own config
    seeds a placeholder too and a whole-page substring check would match it."""
    import re
    for row in re.findall(r"<tr>.*?</tr>", html, re.S):
        if hw in row:
            return row
    return ""


def _write(cache, hw, **fields):
    from homescreen import registry
    records = registry.load(cache)
    records[hw] = fields
    registry.save(cache, records)


def test_a_seeded_placeholder_does_not_read_as_a_panel_that_went_offline(
        ctx, tmp_path):
    client, _, _ = ctx
    _write(tmp_path, "cfg:radar", name="radar", fw="config",
           scene="unassigned", caps={}, telemetry={}, poll_seconds=5,
           first_seen="2026-08-26T16:43:41+02:00",
           last_seen="2026-08-26T16:43:41+02:00")
    assert "sin adoptar" in _row(_home(client), "cfg:radar")


def test_a_real_panel_that_is_offline_still_says_so(ctx, tmp_path):
    # The distinction has to cut both ways, or it is only a relabelling.
    client, _, _ = ctx
    _write(tmp_path, "aabbccddeeff", name="salon", fw="hs-0.1", scene="clock",
           caps={"w": 240, "h": 240, "depth": 16}, telemetry={},
           approved_at="2026-08-01T00:00:00+02:00",
           first_seen="2026-08-01T00:00:00+02:00",
           last_seen="2026-08-02T00:00:00+02:00")
    row = _row(_home(client), "aabbccddeeff")
    assert "sin conexión" in row
    assert "sin adoptar" not in row


def test_a_placeholder_that_has_since_connected_is_an_ordinary_panel(
        ctx, tmp_path):
    # Adoption is what settles it: once a board reports capabilities the
    # record is a panel, whatever its id looks like.
    client, _, _ = ctx
    _write(tmp_path, "cfg:radar", name="radar", fw="hs-0.1", scene="clock",
           caps={"w": 240, "h": 240, "depth": 16}, telemetry={},
           first_seen="2026-08-26T16:43:41+02:00",
           last_seen="2026-08-27T10:00:00+02:00")
    assert "sin adoptar" not in _row(_home(client), "cfg:radar")


def test_a_placeholder_says_what_it_is_on_its_own_page(ctx, tmp_path):
    # The fleet row can only carry a two-word pill. Somebody who clicks
    # through deserves to be told this record came from a file, that no board
    # has ever answered to it, and that removing it is the normal ending.
    client, _, _ = ctx
    _write(tmp_path, "cfg:radar", name="radar", fw="config",
           scene="unassigned", caps={}, telemetry={}, poll_seconds=5,
           first_seen="2026-08-26T16:43:41+02:00",
           last_seen="2026-08-26T16:43:41+02:00")
    html = client.get("/device/cfg:radar").get_data(as_text=True)
    assert "config.yaml" in html
    assert "nunca" in html.lower()
