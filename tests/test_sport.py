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


# --- following a club AND its competition -----------------------------------

def _fx(when, home, away, followed=False, source=""):
    from datetime import datetime, timezone
    return (datetime.fromisoformat(when).replace(tzinfo=timezone.utc),
            {"home": home, "away": away, "followed": followed, "source": source})


def test_a_fixture_returned_by_two_sources_is_one_row():
    # Measured against the live caches: following `Madrid = futbol:86` and
    # `Champions = futbol:CL` made four of thirty-nine fixtures arrive twice,
    # and the block drew each of them as two rows.
    entries = [_fx("2026-09-08T19:00", "Real Madrid", "Inter", True, "Madrid"),
               _fx("2026-09-08T19:00", "Real Madrid", "Inter", False, "Champions"),
               _fx("2026-09-09T19:00", "Club Brugge", "Aston Villa", False, "Champions")]
    out = scenes.sport.dedupe(entries)
    assert len(out) == 2


def test_the_first_source_names_the_row_but_relevance_survives_either_order():
    # Whether your team is playing is a fact about the FIXTURE. It must not
    # depend on which line the user happened to write first.
    both = [_fx("2026-09-08T19:00", "Real Madrid", "Inter", False, "Champions"),
            _fx("2026-09-08T19:00", "Real Madrid", "Inter", True, "Madrid")]
    out = scenes.sport.dedupe(both)
    assert len(out) == 1
    assert out[0][1]["source"] == "Champions", "the first line names it"
    assert out[0][1]["followed"] is True, "and it is still my team's game"


def test_a_club_follow_is_relevant_and_a_competition_follow_is_not():
    assert scenes.sport.is_team("football", {"team": 86}) is True
    assert scenes.sport.is_team("football", {"competition": "CL"}) is False
    assert scenes.sport.is_team("euroleague", {"team": "MAD"}) is True
    assert scenes.sport.is_team("euroleague", {"team": ""}) is False
    assert scenes.sport.is_team("f1", {"season": "current"}) is False


def test_the_block_keeps_room_for_games_my_team_is_not_in():
    # Over thirty days Real Madrid alone has more fixtures than any block has
    # rows, so a pure relevance sort would show nothing else -- which is not
    # what "and other games from champions and euroliga" asked for.
    mine = [_fx(f"2026-09-{d:02d}T19:00", "Real Madrid", f"Rival {d}", True)
            for d in range(2, 12)]
    rest = [_fx(f"2026-09-{d:02d}T17:00", f"A{d}", f"B{d}", False)
            for d in range(2, 12)]
    out = scenes.sport.rank(mine + rest, 5)
    assert len(out) == 5
    assert any(m[1]["followed"] for m in out)
    assert any(not m[1]["followed"] for m in out), "no room left for anything else"


def test_a_ranked_block_still_reads_in_date_order():
    # Relevance decides WHICH are shown; time decides the order shown IN. A
    # list sorted by relevance reads as though the dates are shuffled.
    mine = [_fx("2026-09-20T19:00", "Real Madrid", "Late", True)]
    rest = [_fx("2026-09-03T17:00", "Early", "Game", False)]
    out = scenes.sport.rank(mine + rest, 2)
    assert [m[1]["home"] for m in out] == ["Early", "Real Madrid"]


def test_the_block_fills_up_when_one_side_is_short():
    mine = [_fx("2026-09-02T19:00", "Real Madrid", "X", True)]
    rest = [_fx(f"2026-09-{d:02d}T17:00", f"A{d}", f"B{d}", False)
            for d in range(3, 9)]
    assert len(scenes.sport.rank(mine + rest, 5)) == 5


def test_a_fixture_past_tomorrow_carries_its_date():
    # "mar 18:45" on a panel you glance at could be this Tuesday or the one
    # after, and a fixture list is read to plan around.
    import datetime as _dt
    now = _dt.datetime(2026, 9, 1, 12, 0, tzinfo=_dt.timezone.utc)
    def at(days, hour=18, minute=45):
        return scenes.sport._when(
            now + _dt.timedelta(days=days, hours=hour - 12, minutes=minute),
            now.timestamp())
    assert at(0).startswith("hoy ")
    assert at(1).startswith("mañana ")
    within = at(3)
    assert "/" in within, f"no date in {within!r}"
    assert "4/9" in within
    beyond = at(20)
    assert "21/9" in beyond
    assert "0" not in beyond.split()[0], "no leading zeros in the date"
