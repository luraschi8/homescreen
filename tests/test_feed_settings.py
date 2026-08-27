"""Changing a feed from the dashboard.

The form rendered its current values and the POST returned 405 -- the route was
registered GET-only, so pressing Save produced a browser error page. These are
the tests for the write path that was missing.
"""
import pytest

from homescreen import config, overrides
from homescreen.serve import create_app

CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid",
                    "lat": 40.4, "lon": -3.7},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://old.invalid/api",
                          "fetch_seconds": 3, "api_key": "s3cret-key"}},
       "devices": []}


@pytest.fixture
def ctx(tmp_path):
    return create_app(CFG, tmp_path, version="t").test_client(), tmp_path


def test_saving_an_endpoint_reaches_what_the_fetch_daemon_reads(ctx):
    # The daemon re-reads the override file every cycle, so this is the whole
    # requirement: no restart, no SSH, no config.yaml edit.
    client, cache = ctx
    client.post("/settings", data={"endpoint": "https://new.invalid/api"})
    assert config.feed_config(overrides.apply(CFG, cache))["endpoint"] \
        == "https://new.invalid/api"


def test_saving_a_cadence_reaches_it_too(ctx):
    client, cache = ctx
    client.post("/settings", data={"fetch_seconds": "12"})
    assert config.feed_config(overrides.apply(CFG, cache))["fetch_seconds"] == 12


def test_the_form_shows_what_is_actually_in_use_not_the_file(ctx):
    client, _ = ctx
    client.post("/settings", data={"fetch_seconds": "12"})
    html = client.get("/settings").get_data(as_text=True)
    assert 'value="12"' in html


@pytest.mark.parametrize("form,expected", [
    ({"endpoint": "ftp://x"}, "http://"),
    ({"endpoint": ""}, "vacío"),
    ({"endpoint": "x" * 600}, "supera"),
    ({"fetch_seconds": "0"}, "entre"),
    ({"fetch_seconds": "99999"}, "entre"),
    ({"fetch_seconds": "abc"}, "número"),
])
def test_a_bad_value_is_refused_and_says_why(ctx, form, expected):
    # A human pressed Save and is waiting to be told whether it worked.
    # Silently dropping a typo leaves them staring at a value the daemon is
    # not using.
    client, cache = ctx
    r = client.post("/settings", data=form)
    assert r.status_code in (302, 303)
    assert expected in r.headers["Location"] or expected in \
        __import__("urllib.parse", fromlist=["unquote"]).unquote(r.headers["Location"])
    # and nothing was written
    assert overrides.load(cache).get(overrides.FEEDS_KEY) in (None, {})


def test_the_source_module_cannot_be_switched_from_the_page(ctx):
    # `source` chooses which fetcher module runs. An unauthenticated page must
    # not be able to repoint that.
    client, cache = ctx
    client.post("/settings", data={"source": "something_else",
                                   "fetch_seconds": "9"})
    assert config.feed_config(overrides.apply(CFG, cache))["source"] == "api"


def test_the_api_key_is_neither_settable_nor_rendered(ctx):
    # CLAUDE.md 7.4: the status page is unauthenticated on the LAN and must
    # render config STRUCTURE only.
    client, cache = ctx
    client.post("/settings", data={"api_key": "attacker", "fetch_seconds": "9"})
    assert config.feed_config(overrides.apply(CFG, cache))["api_key"] == "s3cret-key"
    assert "s3cret" not in client.get("/settings").get_data(as_text=True)
    assert "attacker" not in client.get("/settings").get_data(as_text=True)


def test_an_empty_form_changes_nothing_rather_than_erasing_the_feed(ctx):
    client, cache = ctx
    r = client.post("/settings", data={})
    assert "sin+cambios" in r.headers["Location"]
    assert config.feed_config(overrides.apply(CFG, cache))["endpoint"] \
        == "https://old.invalid/api"


def test_a_device_named_feeds_cannot_collide_with_the_feed_settings(ctx):
    # The override file is keyed by device id; feed settings live under a
    # reserved key so a device someone named "feeds" cannot shadow them.
    assert overrides.FEEDS_KEY.startswith("@")


def test_a_corrupt_override_file_degrades_to_the_configured_feed(tmp_path):
    # This file is written from the network, so a bad one must never wedge
    # either daemon.
    overrides.overrides_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    overrides.overrides_path(tmp_path).write_text("{not json")
    assert config.feed_config(overrides.apply(CFG, tmp_path))["endpoint"] \
        == "https://old.invalid/api"


def test_a_feeds_key_holding_nonsense_does_not_take_the_daemon_down(tmp_path):
    overrides.save(tmp_path, {overrides.FEEDS_KEY: "not-a-mapping"})
    assert config.feed_config(overrides.apply(CFG, tmp_path))["endpoint"] \
        == "https://old.invalid/api"
    overrides.save(tmp_path, {overrides.FEEDS_KEY: {"adsb": ["also", "wrong"]}})
    assert config.feed_config(overrides.apply(CFG, tmp_path))["endpoint"] \
        == "https://old.invalid/api"
