"""Deriving fetch work from what screens show, and running it.

The property this whole layer exists for: nobody configures a job. A job is the
consequence of an assignment, so the two can never disagree -- there is no
orphaned job for a screen that was removed, and no screen waiting on a job
nobody created.
"""
import pytest

from homescreen import jobrunner, jobs, jobstore, layout, providers

CFG = {"location": {"lat": 40.4, "lon": -3.7},
       "feeds": {"adsb": {"endpoint": "https://x"}}}


def radar(radius=60):
    return {"scene": "planes", "options": {"radius_km": radius}}


# --- collection -------------------------------------------------------------

def test_screens_wanting_the_same_data_share_one_fetch():
    # Five screens showing Madrid is one request. Nothing upstream should be
    # able to tell how many panels are in the house.
    plan = jobs.collect({f"d{i}": radar(40) for i in range(5)}, CFG)
    assert len(plan) == 1
    assert len(next(iter(plan.values())).wanted_by) == 5


def test_screens_wanting_different_data_are_different_jobs():
    plan = jobs.collect({"a": radar(40), "b": radar(90)}, CFG)
    assert len(plan) == 2
    assert {j.params["radius_km"] for j in plan.values()} == {40.0, 90.0}


def test_a_component_that_needs_nothing_creates_no_job():
    # The clock works with the network down, and must not imply a fetch.
    assert jobs.collect({"a": {"scene": "clock", "options": {}}}, CFG) == {}


def test_a_view_that_is_not_showing_still_has_its_data_fetched():
    # A schedule that switches to the radar at 09:00 must not discover at 09:00
    # that nobody has been fetching. The job exists because the VIEW exists.
    rec = {"scene": "clock",
           "views": {"noche": layout.single("clock"),
                     "dia": layout.single("planes", {"radius_km": 40})},
           "schedule": {"default": "noche", "slots": []}}
    plan = jobs.collect({"a": rec}, CFG)
    assert len(plan) == 1, "the radar's sky is warm before it is needed"


def test_a_removed_screen_takes_its_job_with_it():
    assert jobs.collect({}, CFG) == {}


def test_a_requirement_we_cannot_turn_into_a_fetch_is_dropped():
    # Fetching the wrong thing forever looks healthy, which is worse than not
    # fetching: a job with no centre is not a job.
    assert jobs.collect({"a": radar()}, {"location": {}, "feeds": {}}) == {}


def test_the_shorter_cadence_wins_when_two_screens_share_a_job():
    # Was tautological: both screens asked for the provider default, so
    # min(x, x) == x and `max` passed too. One screen must actually want it
    # sooner for this to say anything.
    def wants(component, options, cfg):
        return ({"provider": "adsb", "params": {"lat": 1, "lon": 2},
                 "interval_s": options.get("every")},)

    plan = jobs.collect({"a": {"scene": "planes", "options": {"every": 60}},
                         "b": {"scene": "planes", "options": {"every": 9}}},
                        CFG, requirements=wants)
    assert next(iter(plan.values())).interval_s == 9


def test_a_job_is_identified_by_its_parameters_not_its_asker():
    a = providers.key("adsb", {"lat": 1, "lon": 2})
    b = providers.key("adsb", {"lon": 2, "lat": 1})
    assert a == b, "key order must not matter"
    assert a != providers.key("adsb", {"lat": 1, "lon": 3})


@pytest.mark.parametrize("records", [None, {}, {"a": None}, {"a": "x"},
                                     {"a": {"scene": "ghost"}},
                                     {"a": {"views": {"v": None}}}])
def test_collection_never_raises_on_a_malformed_fleet(records):
    # One bad assignment must not stop the daemon fetching for every other.
    assert isinstance(jobs.collect(records, CFG), dict)


# --- the provider port ------------------------------------------------------

def test_a_provider_refuses_parameters_it_cannot_use():
    with pytest.raises(ValueError):
        providers.clean_params("adsb", {"lat": 40.4})          # no lon
    with pytest.raises(ValueError):
        providers.clean_params("adsb", {"lat": 999, "lon": 0})


def test_an_endpoint_that_is_not_a_url_is_refused():
    with pytest.raises(ValueError):
        providers.clean_params("adsb", {"lat": 1, "lon": 2,
                                        "endpoint": "javascript:x"})


def test_a_cadence_is_bounded_whatever_is_asked_for():
    assert providers.clamp_interval(0) == providers.MIN_INTERVAL_S
    assert providers.clamp_interval(10 ** 9) == providers.MAX_INTERVAL_S
    assert providers.clamp_interval("soon") == 300


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
    monkeypatch.setattr(providers, "_modules", lambda: {"fake": FakeProvider})
    return FakeProvider


def _job(key="fake-abc123", interval=10, **params):
    return jobs.Job(provider="fake", key=key, params=params,
                    interval_s=interval)


def test_a_job_runs_when_it_is_due_and_not_before(fake, tmp_path):
    plan = {"fake-abc123": _job()}
    last = {}
    assert jobrunner.run_once(tmp_path, plan, last, now=100.0) == 1
    assert jobrunner.run_once(tmp_path, plan, last, now=105.0) == 0
    assert jobrunner.run_once(tmp_path, plan, last, now=110.0) == 1


def test_a_payload_reaches_the_store(fake, tmp_path):
    jobrunner.run_once(tmp_path, {"fake-abc123": _job(v=7)}, {}, now=1.0)
    env = jobstore.read(tmp_path, "fake-abc123")
    assert env["data"] == {"value": 7} and env["ok"] is True


def test_a_failing_job_keeps_the_last_good_payload(fake, tmp_path):
    # Blanking on one timeout is how a panel goes empty during a hiccup.
    jobrunner.run_once(tmp_path, {"fake-abc123": _job(v=7)}, {}, now=1.0)
    jobrunner.run_once(tmp_path, {"fake-abc123": _job(v=7, boom=True)},
                       {}, now=100.0)
    env = jobstore.read(tmp_path, "fake-abc123")
    assert env["data"] == {"value": 7}, "the last good sky survives"
    assert env["ok"] is False
    assert "down" in (env.get("error") or "")


def test_one_provider_being_down_does_not_stop_the_others(fake, tmp_path):
    plan = {"fake-aaa111": _job("fake-aaa111", boom=True),
            "fake-bbb222": _job("fake-bbb222", v=2)}
    jobrunner.run_once(tmp_path, plan, {}, now=1.0)
    assert jobstore.read(tmp_path, "fake-bbb222")["data"] == {"value": 2}


def test_payloads_nobody_wants_are_pruned(fake, tmp_path):
    jobrunner.run_once(tmp_path, {"fake-abc123": _job()}, {}, now=1.0)
    assert jobstore.read(tmp_path, "fake-abc123") is not None
    assert jobstore.prune(tmp_path, keep=set()) == 1
    assert jobstore.read(tmp_path, "fake-abc123") is None


def test_a_job_key_that_could_be_a_path_is_refused():
    # The key reaches this module from stored records and becomes a filename.
    for bad in ("../../etc/passwd", "a/b", "", "fake-../x", "fake-ZZZZ"):
        with pytest.raises(ValueError):
            jobstore.path_for("/tmp", bad)


def test_the_loop_sleeps_until_the_soonest_job_is_due(fake, tmp_path):
    # Was: an empty fleet, so `plan` was empty, `_nap` returned its hardcoded
    # fallback, and `all(n > 0)` was unfalsifiable because _nap floors at 0.5.
    # It passed with the whole function deleted.
    plan = {"fake-aaa111": _job("fake-aaa111", interval=10),
            "fake-bbb222": _job("fake-bbb222", interval=90)}
    last = {"fake-aaa111": 994.0, "fake-bbb222": 1000.0}
    assert jobrunner._nap(plan, last, 1000.0) == pytest.approx(4.0), \
        "the sooner job is due in 4s, not the later one in 90"


def test_the_loop_does_not_sleep_past_a_reload(fake, tmp_path):
    # Assignments change while this runs; an hourly job must not mean an hour
    # before noticing a new screen.
    plan = {"fake-aaa111": _job("fake-aaa111", interval=3600)}
    assert jobrunner._nap(plan, {"fake-aaa111": 1000.0}, 1000.0) \
        <= jobrunner.RELOAD_EVERY_S


def test_the_loop_notices_a_screen_added_while_it_runs(fake, tmp_path,
                                                       monkeypatch):
    # Somebody is on the dashboard. A daemon that only learns about a new
    # screen on restart is one someone has to remember to restart.
    monkeypatch.setattr(providers, "_modules",
                        lambda: {"adsb": __import__(
                            "homescreen.providers.adsb", fromlist=["x"])})
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
    real_collect = jobs.collect

    def watched(*a, **kw):
        plan = real_collect(*a, **kw)
        plans.append(plan)
        return plan

    monkeypatch.setattr(jobs, "collect", watched)
    jobrunner.run_forever(lambda: CFG, records, tmp_path, cycles=4,
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
