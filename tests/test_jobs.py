"""Deriving fetch work from what screens show, and running it.

The property this whole layer exists for: nobody configures a job. A job is the
consequence of an assignment, so the two can never disagree -- there is no
orphaned job for a screen that was removed, and no screen waiting on a job
nobody created.
"""
import pytest

from homescreen import fetch, layout

CFG = {"location": {"lat": 40.4, "lon": -3.7},
       "feeds": {"adsb": {"endpoint": "https://x"}}}


def radar(radius=60):
    return {"scene": "planes", "options": {"radius_km": radius}}


# --- collection -------------------------------------------------------------

def test_screens_wanting_the_same_data_share_one_fetch():
    # Five screens showing Madrid is one request. Nothing upstream should be
    # able to tell how many panels are in the house.
    plan = fetch.derive({f"d{i}": radar(40) for i in range(5)}, CFG)
    assert len(plan) == 1
    assert len(next(iter(plan.values())).wanted_by) == 5


def test_screens_wanting_different_data_are_different_jobs():
    plan = fetch.derive({"a": radar(40), "b": radar(90)}, CFG)
    assert len(plan) == 2
    assert {j.params["radius_km"] for j in plan.values()} == {40.0, 90.0}


def test_a_component_that_needs_nothing_creates_no_job():
    # The clock works with the network down, and must not imply a fetch.
    assert fetch.derive({"a": {"scene": "clock", "options": {}}}, CFG) == {}


def test_a_view_that_is_not_showing_still_has_its_data_fetched():
    # A schedule that switches to the radar at 09:00 must not discover at 09:00
    # that nobody has been fetching. The job exists because the VIEW exists.
    rec = {"scene": "clock",
           "views": {"noche": layout.single("clock"),
                     "dia": layout.single("planes", {"radius_km": 40})},
           "schedule": {"default": "noche", "slots": []}}
    plan = fetch.derive({"a": rec}, CFG)
    assert len(plan) == 1, "the radar's sky is warm before it is needed"


def test_a_removed_screen_takes_its_job_with_it():
    assert fetch.derive({}, CFG) == {}


def test_a_requirement_we_cannot_turn_into_a_fetch_is_dropped():
    # Fetching the wrong thing forever looks healthy, which is worse than not
    # fetching: a job with no centre is not a job.
    assert fetch.derive({"a": radar()}, {"location": {}, "feeds": {}}) == {}


def test_the_shorter_cadence_wins_when_two_screens_share_a_job():
    # Was tautological: both screens asked for the provider default, so
    # min(x, x) == x and `max` passed too. One screen must actually want it
    # sooner for this to say anything.
    def wants(component, options, cfg):
        return ({"provider": "adsb",
                 "params": {"lat": 1, "lon": 2, "endpoint": "https://x"},
                 "interval_s": options.get("every")},)

    plan = fetch.derive({"a": {"scene": "planes", "options": {"every": 60}},
                         "b": {"scene": "planes", "options": {"every": 9}}},
                        CFG, requirements=wants)
    assert next(iter(plan.values())).interval_s == 9


def test_a_job_is_identified_by_its_parameters_not_its_asker():
    a = fetch.providers.key("adsb", {"lat": 1, "lon": 2})
    b = fetch.providers.key("adsb", {"lon": 2, "lat": 1})
    assert a == b, "key order must not matter"
    assert a != fetch.providers.key("adsb", {"lat": 1, "lon": 3})


@pytest.mark.parametrize("records", [None, {}, {"a": None}, {"a": "x"},
                                     {"a": {"scene": "ghost"}},
                                     {"a": {"views": {"v": None}}}])
def test_collection_never_raises_on_a_malformed_fleet(records):
    # One bad assignment must not stop the daemon fetching for every other.
    assert isinstance(fetch.derive(records, CFG), dict)


# --- the provider port ------------------------------------------------------

def test_a_provider_refuses_parameters_it_cannot_use():
    with pytest.raises(ValueError):
        fetch.providers.clean_params("adsb", {"lat": 40.4})          # no lon
    with pytest.raises(ValueError):
        fetch.providers.clean_params("adsb", {"lat": 999, "lon": 0})


def test_an_endpoint_that_is_not_a_url_is_refused():
    with pytest.raises(ValueError):
        fetch.providers.clean_params("adsb", {"lat": 1, "lon": 2,
                                        "endpoint": "javascript:x"})


def test_a_cadence_is_bounded_whatever_is_asked_for():
    assert fetch.providers.clamp_interval(0) == fetch.providers.MIN_INTERVAL_S
    assert fetch.providers.clamp_interval(10 ** 9) == fetch.providers.MAX_INTERVAL_S
    assert fetch.providers.clamp_interval("soon") == 300


# --- running ----------------------------------------------------------------

class FakeProvider:
    NAME = "fake"
    DEFAULT_INTERVAL_S = 10
    calls = 0

    @staticmethod
    def fetch(params, *, session=None, secrets=None):
        FakeProvider.calls += 1
        if params.get("boom"):
            raise RuntimeError("upstream is down")
        return {"value": params.get("v", 1)}


@pytest.fixture
def fake(monkeypatch):
    FakeProvider.calls = 0
    monkeypatch.setattr(fetch.providers, "_modules",
                        lambda: {"fake": FakeProvider})
    return FakeProvider


def _job(key="fake-abc123", interval=10, **params):
    return fetch.Job(provider="fake", key=key, params=params,
                    interval_s=interval)


def test_a_job_runs_when_it_is_due_and_not_before(fake, tmp_path):
    plan = {"fake-abc123": _job()}
    last = {}
    assert fetch.runner.run_once(tmp_path, plan, last, now=100.0) == 1
    assert fetch.runner.run_once(tmp_path, plan, last, now=105.0) == 0
    assert fetch.runner.run_once(tmp_path, plan, last, now=110.0) == 1


def test_a_payload_reaches_the_store(fake, tmp_path):
    fetch.runner.run_once(tmp_path, {"fake-abc123": _job(v=7)}, {}, now=1.0)
    env = fetch.store.read(tmp_path, "fake-abc123")
    assert env["data"] == {"value": 7} and env["ok"] is True


def test_a_failing_job_keeps_the_last_good_payload(fake, tmp_path):
    # Blanking on one timeout is how a panel goes empty during a hiccup.
    fetch.runner.run_once(tmp_path, {"fake-abc123": _job(v=7)}, {}, now=1.0)
    fetch.runner.run_once(tmp_path, {"fake-abc123": _job(v=7, boom=True)},
                       {}, now=100.0)
    env = fetch.store.read(tmp_path, "fake-abc123")
    assert env["data"] == {"value": 7}, "the last good sky survives"
    assert env["ok"] is False
    assert "down" in (env.get("error") or "")


def test_one_provider_being_down_does_not_stop_the_others(fake, tmp_path):
    plan = {"fake-aaa111": _job("fake-aaa111", boom=True),
            "fake-bbb222": _job("fake-bbb222", v=2)}
    fetch.runner.run_once(tmp_path, plan, {}, now=1.0)
    assert fetch.store.read(tmp_path, "fake-bbb222")["data"] == {"value": 2}


def test_payloads_nobody_wants_are_pruned(fake, tmp_path):
    fetch.runner.run_once(tmp_path, {"fake-abc123": _job()}, {}, now=1.0)
    assert fetch.store.read(tmp_path, "fake-abc123") is not None
    assert fetch.store.prune(tmp_path, keep=set()) == 1
    assert fetch.store.read(tmp_path, "fake-abc123") is None


def test_a_job_key_that_could_be_a_path_is_refused():
    # The key reaches this module from stored records and becomes a filename.
    for bad in ("../../etc/passwd", "a/b", "", "fake-../x", "fake-ZZZZ"):
        with pytest.raises(ValueError):
            fetch.store.path_for("/tmp", bad)


def test_the_loop_sleeps_until_the_soonest_job_is_due(fake, tmp_path):
    # Was: an empty fleet, so `plan` was empty, `_nap` returned its hardcoded
    # fallback, and `all(n > 0)` was unfalsifiable because _nap floors at 0.5.
    # It passed with the whole function deleted.
    plan = {"fake-aaa111": _job("fake-aaa111", interval=10),
            "fake-bbb222": _job("fake-bbb222", interval=90)}
    last = {"fake-aaa111": 994.0, "fake-bbb222": 1000.0}
    assert fetch.runner._nap(plan, last, 1000.0) == pytest.approx(4.0), \
        "the sooner job is due in 4s, not the later one in 90"


def test_the_loop_does_not_sleep_past_a_reload(fake, tmp_path):
    # Assignments change while this runs; an hourly job must not mean an hour
    # before noticing a new screen.
    plan = {"fake-aaa111": _job("fake-aaa111", interval=3600)}
    assert fetch.runner._nap(plan, {"fake-aaa111": 1000.0}, 1000.0) \
        <= fetch.runner.RELOAD_EVERY_S


def test_the_loop_notices_a_screen_added_while_it_runs(fake, tmp_path,
                                                       monkeypatch):
    # Somebody is on the dashboard. A daemon that only learns about a new
    # screen on restart is one someone has to remember to restart.
    monkeypatch.setattr(fetch.providers, "_modules",
                        lambda: {"adsb": __import__(
                            "homescreen.fetch.providers.adsb", fromlist=["x"])})
    fleet = {}
    seen = []

    def records():
        seen.append(len(fleet))
        return dict(fleet)

    def tick():
        tick.t += 60.0
        if tick.t > 1100:
            fleet["a"] = radar(40)
        return tick.t
    tick.t = 1000.0

    plans = []
    from homescreen.fetch import plan as planning
    real_collect = planning.collect

    def watched(*a, **kw):
        got = real_collect(*a, **kw)
        plans.append(got)
        return got

    # Patched where the runner LOOKS it up, not where it is defined -- the
    # runner imported the module, so patching the package surface would leave
    # it calling the original.
    monkeypatch.setattr(planning, "collect", watched)
    fetch.runner.run_forever(lambda: CFG, records, tmp_path, cycles=4,
                          sleep=lambda s: None, clock=tick,
                          session=_DeadSession())
    # Was: `seen[-1] == 1`, which proves only that records_loader was called
    # again -- it passed with collect stubbed to return nothing. Assert the
    # JOB appeared.
    assert not plans[0], "nothing to fetch before the screen existed"
    assert plans[-1], "the new screen's job was picked up without a restart"


class _DeadSession:
    def get(self, *a, **k):
        raise RuntimeError("no network in tests")


# --- the port every adapter must satisfy ------------------------------------

@pytest.mark.parametrize("name", fetch.providers.names())
def test_every_registered_provider_satisfies_the_port(name):
    """The contract test the port was missing.

    `getattr` defaults made the port unfalsifiable: an object with only
    `fetch` satisfied every accessor, so an adapter that forgot `clean_params`
    got no validation at all while the docstring promised it raises. This
    fails at registration instead of at three in the morning.
    """
    provider = fetch.providers.get(name)
    assert provider is not None
    assert isinstance(provider, fetch.providers.ProviderPort)
    assert fetch.providers.NAME_RE.match(provider.NAME), \
        "a provider name becomes part of a job key, which becomes a filename"
    assert isinstance(provider.PARAMS, tuple)
    assert isinstance(provider.SECRETS, tuple)
    for secret in provider.SECRETS:
        from homescreen import secrets as secret_store
        assert secret_store.NAME_RE.match(secret), secret
    interval = fetch.providers.default_interval(name)
    assert fetch.providers.MIN_INTERVAL_S <= interval <= fetch.providers.MAX_INTERVAL_S


@pytest.mark.parametrize("name", fetch.providers.names())
def test_every_provider_mints_a_key_its_own_store_accepts(name):
    # These two agreed only by luck: `key()` had no constraint on the provider
    # name and the store's regex refused anything outside [a-z0-9_]. A
    # provider called `open-meteo` produced a key the store rejected, and the
    # rejection double-faulted out of the runner.
    sample = _SAMPLE_PARAMS[name]
    params = fetch.providers.clean_params(name, sample)
    key = fetch.providers.key(name, params)
    assert fetch.store.path_for("/tmp", key).name.endswith(".json")


@pytest.mark.parametrize("name", fetch.providers.names())
def test_cleaning_parameters_twice_changes_nothing(name):
    # Job identity is the cleaned parameters. If cleaning is not idempotent the
    # key moves under a job that has not changed, and its cached payload is
    # orphaned on every cycle.
    once = fetch.providers.clean_params(name, _SAMPLE_PARAMS[name])
    assert fetch.providers.clean_params(name, once) == once


#: Enough to construct a valid job for each provider. A new provider that
#: forgets to add itself here fails the tests above by KeyError, which is the
#: intent -- the sample is part of the contract.
_SAMPLE_PARAMS = {
    "adsb": {"lat": 40.4, "lon": -3.7, "radius_km": 60,
             "endpoint": "https://example.invalid/api"},
    "openweather": {"lat": 40.4, "lon": -3.7, "units": "metric"},
    "quotes": {"symbol": "AAPL"},
}


# --- the guarantees the docstrings make -------------------------------------

@pytest.mark.parametrize("rec", [
    {"views": {1: {"placements": []}, "a": {"placements": []}}},   # mixed keys
    {"views": {"a": {"placements": "not a list"}}},
    {"views": {"a": {"placements": ["a string"]}}},
    {"views": {"a": {"placements": [{"component": 5}]}}},
])
def test_deriving_a_plan_never_raises_on_a_hand_edited_record(rec):
    # This runs on the serve path (GET /api/jobs) over a file a human can edit.
    # A record with an integer view key used to raise a TypeError out of a
    # function documented as never raising.
    assert isinstance(fetch.derive({"aa": rec}, CFG), dict)


def test_recording_a_failure_cannot_itself_take_the_daemon_down(fake, tmp_path,
                                                                monkeypatch):
    # The handler calling record_failure was the LAST guard, so its own
    # exception escaped into a Restart=always loop. A read-only filesystem is
    # the exact Pi fault this protects against.
    def boom(*a, **k):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(fetch.store, "record_failure", boom)
    plan = {"fake-abc123": _job(boom=True)}
    assert fetch.runner.run_once(tmp_path, plan, {}, now=1.0) == 1


def test_a_job_naming_a_provider_that_vanished_does_not_pin_the_loop(fake,
                                                                     tmp_path):
    # A partial deploy can remove a provider while the daemon runs. Skipping
    # without recording the attempt made _nap compute a negative wait and spin
    # the loop at its floor, with nothing in the log to explain it.
    plan = {"ghost-abc123": fetch.Job(provider="ghost", key="ghost-abc123",
                                      params={}, interval_s=60)}
    last = {}
    fetch.runner.run_once(tmp_path, plan, last, now=1000.0)
    assert last["ghost-abc123"] == 1000.0, "the attempt was recorded"
    assert fetch.runner._nap(plan, last, 1000.0) > 1.0, "so it backs off"


def test_requests_to_one_upstream_are_spaced_as_that_upstream_requires(tmp_path,
                                                                       monkeypatch):
    # adsb.fi permits one request a second, enforced as SPACING. Five radars on
    # five centres each politely scheduled still fire together, so the runner
    # must space them -- a per-job cadence cannot express this.
    class Polite:
        NAME = "polite"
        PARAMS = ()
        SECRETS = ()
        DEFAULT_INTERVAL_S = 5
        MIN_SPACING_S = 1.0

        @staticmethod
        def clean_params(raw):
            return dict(raw)

        @staticmethod
        def fetch(params, *, session=None, secrets=None):
            return {"ok": 1}

    monkeypatch.setattr(fetch.providers, "_modules", lambda: {"polite": Polite})
    slept = []
    plan = {f"polite-{i:06x}": fetch.Job(provider="polite", key=f"polite-{i:06x}",
                                         params={"n": i}, interval_s=5)
            for i in range(4)}
    fetch.runner.run_once(tmp_path, plan, {}, now=1.0, sleep=slept.append)
    assert len(slept) >= 3, "three gaps between four requests"
    assert all(s <= 1.0 for s in slept)


def test_a_provider_with_no_stated_limit_is_not_slowed_down(fake, tmp_path):
    slept = []
    plan = {f"fake-{i:06x}": _job(f"fake-{i:06x}", v=i) for i in range(4)}
    fetch.runner.run_once(tmp_path, plan, {}, now=1.0, sleep=slept.append)
    assert slept == []
