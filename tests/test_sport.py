"""The next match, or the last result -- decided from the data, not a setting."""
import datetime
import pathlib
import tempfile

import pytest

from homescreen import fetch, scenes
from homescreen.reading import Reading

NOW = datetime.datetime.fromisoformat("2026-08-28T10:00:00+02:00").timestamp()
PAST = {"when": "2026-08-26T19:00:00Z", "home": "Real Madrid", "away": "Betis",
        "status": "FINISHED", "home_goals": 2, "away_goals": 1,
        "competition": "La Liga"}
NEXT = {"when": "2026-08-30T18:30:00Z", "home": "Getafe", "away": "Real Madrid",
        "status": "TIMED", "competition": "La Liga"}
ROUND = {"w": 240, "h": 240, "depth": 16, "shape": "round"}


def drawn(matches, caps=ROUND, options=None):
    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=caps, now=NOW,
        device={}, options={"team": 86, **(options or {})},
        data=lambda req: Reading(data={"matches": matches}, ok=True))
    return [d["v"] for d in scenes.build("sport", ctx).components[0]["draw"]]


def test_before_kick_off_it_shows_the_fixture():
    values = drawn([PAST, NEXT])
    assert "vs" in values and "2 - 1" not in values


def test_a_match_in_play_beats_everything():
    live = dict(NEXT, status="IN_PLAY", home_goals=0, away_goals=1)
    values = drawn([PAST, live])
    assert "en juego" in values and "0 - 1" in values


def test_with_nothing_upcoming_it_shows_the_last_result():
    # Better than an empty screen, and it is what you want on a Monday.
    values = drawn([PAST])
    assert "2 - 1" in values


def test_which_of_the_two_is_not_a_setting():
    # Asking someone to toggle fixture/result twice a week is asking them to
    # do the computer's job.
    assert scenes.option_schema("sport")
    assert not any(f["key"] in ("mode", "show_result")
                   for f in scenes.option_schema("sport"))


def test_with_no_team_it_says_what_to_do():
    values = drawn([], options={"team": 0})
    assert any("sin equipo" in v for v in values)
    assert scenes.needs("sport", {"team": 0}, {}) == ()


def test_nothing_in_the_window_is_not_a_broken_screen():
    assert any("sin partidos" in v for v in drawn([]))


def test_two_screens_on_two_teams_are_two_fetches():
    plan = fetch.derive({"a": {"scene": "sport", "options": {"team": 86}},
                         "b": {"scene": "sport", "options": {"team": 81}}}, {})
    assert len(plan) == 2


def test_two_screens_on_one_team_share_a_fetch():
    plan = fetch.derive({"a": {"scene": "sport", "options": {"team": 86}},
                         "b": {"scene": "sport", "options": {"team": 86}}}, {})
    assert len(plan) == 1


# --- the provider -----------------------------------------------------------

class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _Session:
    def __init__(self, body):
        self._body = body
        self.headers = None

    def get(self, url, **kw):
        self.headers = kw.get("headers")
        return _Resp(self._body)


def test_the_short_name_is_preferred_because_the_legal_one_does_not_fit():
    from homescreen.fetch.providers import football
    session = _Session({"matches": [{
        "utcDate": "2026-08-30T18:30:00Z", "status": "TIMED",
        "homeTeam": {"name": "Getafe Club de Fútbol S.A.D.",
                     "shortName": "Getafe"},
        "awayTeam": {"name": "Real Madrid Club de Fútbol",
                     "shortName": "Real Madrid"},
        "score": {"fullTime": {"home": None, "away": None}}}]})
    got = football.fetch({"team": 86, "days": 30}, session=session,
                         secrets={"api_key": "k"})
    assert got["matches"][0]["home"] == "Getafe"


def test_the_key_travels_as_a_header_not_a_query_parameter():
    # A token in a URL ends up in logs and referrers.
    from homescreen.fetch.providers import football
    session = _Session({"matches": []})
    football.fetch({"team": 86}, session=session, secrets={"api_key": "sk-x"})
    assert session.headers.get("X-Auth-Token") == "sk-x"


def test_a_response_that_is_not_a_fixture_list_is_a_failure():
    from homescreen.fetch.providers import football
    with pytest.raises(ValueError):
        football.fetch({"team": 86}, session=_Session({"message": "forbidden"}),
                       secrets={"api_key": "k"})


def test_the_provider_refuses_to_fetch_without_its_key():
    from homescreen.fetch.providers import football
    with pytest.raises(ValueError, match="clave"):
        football.fetch({"team": 86}, secrets={})


@pytest.mark.parametrize("bad", [{}, {"team": 0}, {"team": "x"},
                                 {"team": 10 ** 9}])
def test_a_team_that_is_not_a_team_is_refused(bad):
    with pytest.raises(ValueError):
        fetch.providers.clean_params("football", bad)
