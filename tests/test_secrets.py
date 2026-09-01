"""Credentials: settable, never readable.

The claim is structural, not procedural. It is not "the usual response redacts
the value" -- there is no code path that returns one, so a route added next
month cannot leak a key by forgetting to redact. These tests are what keeps
that true as the API grows.
"""
import json
import os

import pytest

from homescreen import secrets
from homescreen.serve import create_app

CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid",
                    "lat": 40.4, "lon": -3.7},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}


@pytest.fixture
def ctx(tmp_path):
    return create_app(CFG, tmp_path, version="t").test_client(), tmp_path


# --- the store --------------------------------------------------------------

def test_a_stored_secret_reports_that_it_is_set_and_not_what_it_is(tmp_path):
    state = secrets.set_secret(tmp_path, "adsb", "api_key", "sk-live-123")
    assert state["set"] is True
    assert "value" not in state, "absent, not null -- nothing to serialise"
    assert "sk-live" not in json.dumps(state)


def test_the_fetcher_can_get_the_value_and_only_the_fetcher(tmp_path):
    secrets.set_secret(tmp_path, "adsb", "api_key", "sk-live-123")
    assert secrets.for_provider(tmp_path, "adsb") == {"api_key": "sk-live-123"}


def test_the_file_is_never_world_readable_even_for_an_instant(tmp_path):
    # Created 0600 before anything is written. Writing first and chmod-ing
    # after leaves a window, and on a box whose security model is "the LAN is
    # trusted" that window is the one thing that must be closed.
    secrets.set_secret(tmp_path, "adsb", "api_key", "x")
    mode = os.stat(secrets.secrets_path(tmp_path)).st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_secrets_live_outside_the_hand_edited_config(tmp_path):
    secrets.set_secret(tmp_path, "adsb", "api_key", "x")
    assert secrets.secrets_path(tmp_path).name != "config.yaml"
    assert secrets.secrets_path(tmp_path).parent == tmp_path


def test_clearing_removes_it(tmp_path):
    secrets.set_secret(tmp_path, "adsb", "api_key", "x")
    assert secrets.clear(tmp_path, "adsb", "api_key") is True
    assert secrets.status(tmp_path, "adsb", "api_key")["set"] is False
    assert secrets.for_provider(tmp_path, "adsb") == {}
    assert secrets.clear(tmp_path, "adsb", "api_key") is False


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_an_empty_value_is_refused_rather_than_stored(tmp_path, bad):
    # Otherwise "saving" a blank field silently unsets a working key.
    with pytest.raises(ValueError):
        secrets.set_secret(tmp_path, "adsb", "api_key", bad)


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b", "A-B", "", "x" * 50])
def test_a_name_that_could_be_a_path_is_refused(tmp_path, name):
    with pytest.raises(ValueError):
        secrets.set_secret(tmp_path, "adsb", name, "v")
    with pytest.raises(ValueError):
        secrets.set_secret(tmp_path, name, "api_key", "v")


def test_a_corrupt_file_degrades_to_nothing_set(tmp_path):
    secrets.secrets_path(tmp_path).write_text("{not json")
    assert secrets.status(tmp_path, "adsb", "api_key")["set"] is False
    assert secrets.for_provider(tmp_path, "adsb") == {}


def test_the_number_of_providers_holding_secrets_is_bounded(tmp_path):
    for i in range(secrets.MAX_SECRETS + 5):
        try:
            secrets.set_secret(tmp_path, f"p{i}", "api_key", "v")
        except ValueError:
            break
    assert len(secrets._load(tmp_path)) <= secrets.MAX_SECRETS


# --- through the API --------------------------------------------------------

def test_no_route_returns_a_secret_value(ctx):
    client, cache = ctx
    secrets.set_secret(cache, "adsb", "api_key", "sk-live-canary")
    for path in ("/api/providers", "/api/status", "/api/devices", "/api/jobs",
                 "/settings", "/"):
        body = client.get(path).get_data(as_text=True)
        assert "sk-live-canary" not in body, path


def test_the_dashboard_can_see_that_a_key_is_set_without_seeing_it(ctx,
                                                                  monkeypatch):
    from homescreen.fetch import providers
    monkeypatch.setattr(providers, "secrets_for", lambda name: ("api_key",))
    client, cache = ctx
    before = client.get("/api/providers").get_json()["providers"][0]["secrets"]
    assert before[0]["set"] is False
    secrets.set_secret(cache, "adsb", "api_key", "sk-live-canary")
    after = client.get("/api/providers").get_json()["providers"][0]["secrets"]
    assert after[0]["set"] is True and "value" not in after[0]


def test_a_secret_a_provider_does_not_use_is_refused(ctx):
    client, _ = ctx
    r = client.put("/api/providers/adsb/secrets/api_key",
                   json={"value": "x"})
    assert r.status_code == 404, "adsb declares no secrets"


def test_setting_a_secret_for_an_unknown_provider_is_a_404(ctx):
    client, cache = ctx
    assert client.put("/api/providers/ghost/secrets/api_key",
                      json={"value": "x"}).status_code == 404
    assert secrets._load(cache) == {}


def test_a_secret_can_be_set_and_cleared_over_the_api(ctx, monkeypatch):
    from homescreen.fetch import providers
    monkeypatch.setattr(providers, "secrets_for", lambda name: ("api_key",))
    client, cache = ctx
    r = client.put("/api/providers/adsb/secrets/api_key",
                   json={"value": "sk-live-canary"})
    assert r.status_code == 200 and r.get_json()["set"] is True
    assert secrets.for_provider(cache, "adsb")["api_key"] == "sk-live-canary"
    assert client.delete("/api/providers/adsb/secrets/api_key").status_code == 204
    assert secrets.for_provider(cache, "adsb") == {}


# --- the field on the page --------------------------------------------------

def test_the_page_offers_a_field_for_each_credential_a_provider_needs(ctx):
    client, _ = ctx
    html = client.get("/settings").get_data(as_text=True)
    assert "openweather · api_key" in html
    assert "sin configurar" in html


def test_saving_a_key_from_the_page_reaches_the_fetcher(ctx):
    client, cache = ctx
    r = client.post("/settings/secrets", data={
        "provider": "openweather", "secret": "api_key",
        "value": "sk-canary-999"})
    assert r.status_code in (302, 303)
    assert secrets.for_provider(cache, "openweather") == {"api_key": "sk-canary-999"}


def test_the_field_never_contains_the_key_even_once_it_is_set(ctx):
    # There is nothing to put in it: no route returns a value. What it shows
    # instead is that one is set and when.
    client, cache = ctx
    client.post("/settings/secrets", data={
        "provider": "openweather", "secret": "api_key", "value": "sk-canary-999"})
    html = client.get("/settings").get_data(as_text=True)
    assert "sk-canary-999" not in html
    assert "Guardada el" in html


def test_the_page_can_clear_a_key(ctx):
    client, cache = ctx
    client.post("/settings/secrets", data={
        "provider": "openweather", "secret": "api_key", "value": "x"})
    client.post("/settings/secrets", data={
        "provider": "openweather", "secret": "api_key", "action": "clear"})
    assert secrets.for_provider(cache, "openweather") == {}


def test_a_blank_save_does_not_silently_unset_a_working_key(ctx):
    client, cache = ctx
    client.post("/settings/secrets", data={
        "provider": "openweather", "secret": "api_key", "value": "working"})
    r = client.post("/settings/secrets", data={
        "provider": "openweather", "secret": "api_key", "value": ""})
    assert secrets.for_provider(cache, "openweather") == {"api_key": "working"}
    assert "vac" in r.headers["Location"], "and it says why"


@pytest.mark.parametrize("form", [
    {"provider": "ghost", "secret": "api_key", "value": "x"},
    {"provider": "openweather", "secret": "otra", "value": "x"},
    {"provider": "", "secret": "", "value": "x"},
])
def test_a_credential_that_does_not_exist_is_refused(ctx, form):
    client, cache = ctx
    r = client.post("/settings/secrets", data=form)
    assert r.status_code in (302, 303)
    assert secrets._load(cache) == {}


# --- a failing request must not publish the key -----------------------------

def test_a_failed_fetch_does_not_write_the_key_into_the_job_store(tmp_path,
                                                                  monkeypatch):
    """Found by running a real request against Finnhub.

    It answered 403 and `requests` put the whole URL -- including
    `&token=...` -- into the exception. That string goes into the job
    envelope, which /api/jobs and the settings page render on an
    unauthenticated LAN dashboard. One failed fetch published the key.
    """
    from homescreen import fetch

    class Leaky:
        NAME = "leaky"
        PARAMS = ()
        SECRETS = ("api_key",)
        DEFAULT_INTERVAL_S = 60

        @staticmethod
        def clean_params(raw):
            return dict(raw)

        @staticmethod
        def fetch(params, *, session=None, secrets=None):
            raise RuntimeError(
                "403 for url: https://v/api?symbol=AAPL&token=SUPERSECRET123")

    monkeypatch.setattr(fetch.providers, "_modules", lambda: {"leaky": Leaky})
    job = fetch.Job(provider="leaky", key="leaky-abc123", params={},
                    interval_s=60)
    fetch.runner.run_once(tmp_path, {"leaky-abc123": job}, {}, now=1.0,
                          secrets_for=lambda n, s=None: {"api_key": "SUPERSECRET123"})
    env = fetch.read(tmp_path, "leaky-abc123")
    assert "SUPERSECRET123" not in json.dumps(env)
    assert "oculto" in env["error"]


@pytest.mark.parametrize("param", ["token", "key", "apikey", "appid",
                                   "access_key", "api_key"])
def test_every_common_credential_parameter_is_redacted(param):
    from homescreen.fetch.runner import redact
    got = redact(f"error for https://v/api?x=1&{param}=SUPERSECRET123")
    assert "SUPERSECRET123" not in got


def test_a_key_quoted_back_in_prose_is_redacted_too():
    # The URL pattern cannot catch a vendor echoing the key in its own message.
    from homescreen.fetch.runner import redact
    got = redact("your key sk-abcdefgh is invalid", ["sk-abcdefgh"])
    assert "sk-abcdefgh" not in got


def test_redaction_does_not_mangle_an_ordinary_message():
    from homescreen.fetch.runner import redact
    assert redact("connection timed out", ["k"]) == "connection timed out"


def test_a_scope_accepts_every_view_name_the_ui_blesses():
    # `safe_view_name` deliberately keeps accents -- the UI is Spanish and
    # folding `mañana` to ASCII trades a legibility bug for nothing. The scope
    # pattern then rejected exactly those names, so the per-screen credential
    # form rendered, saved nothing, and reported an internal-sounding error.
    from homescreen.web.views_ui import safe_view_name
    for raw in ("panel", "mañana", "Casa · Día", "Día 1", "x" * 24):
        name = safe_view_name(raw)
        scope = f"aabbccddeeff/{name}/p1/openweather"
        assert secrets.SCOPE_RE.match(scope), (raw, name, scope)


def test_a_scope_still_cannot_contain_the_separator():
    # The one character it must never hold: the split would be ambiguous and
    # a credential would be read back under the wrong placement.
    assert not secrets.SCOPE_RE.match("aabb/one@two/p1/openweather")


def test_a_scope_rejects_control_characters_and_absurd_length():
    assert not secrets.SCOPE_RE.match("aabb/one\x00two/p/openweather")
    assert not secrets.SCOPE_RE.match("x" * 200)
