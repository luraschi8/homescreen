"""Several teams, several sports, one block.

`sport` followed one football team. Following a second meant a second
placement, which is a second block to read -- and nothing could follow a sport
football-data.org does not cover.
"""
import pathlib
import re
import tempfile

import pytest

from homescreen import scenes
from homescreen.reading import Reading
from homescreen.fetch.providers import _fixtures, f1, nba


def _ctx(options, data, caps=None):
    return scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps=caps or {"w": 417, "h": 200, "depth": 1}, now=1_788_260_000.0,
        device={}, data=data,
        options=scenes.clean_options("sport", options))


# --- what a line can name -----------------------------------------------------

def test_a_line_names_a_source_and_a_team():
    from homescreen.scenes import sport
    got = sport.needs({"teams": "Madrid = futbol:86\nLakers = nba:LAL\nF1 = f1"},
                      {})
    assert [n["provider"] for n in got] == ["football", "nba", "f1"]
    assert got[0]["params"]["team"] == 86
    assert got[1]["params"]["team"] == "LAL"


def test_a_source_we_cannot_fetch_is_dropped_not_guessed_at():
    from homescreen.scenes import sport
    assert sport.needs({"teams": "X = cricket:5"}, {}) == ()
    # Letters are read as a COMPETITION code, so this is refused for being
    # the wrong length rather than for not being a number -- codes are 2-5
    # characters and truncating a longer one produces a 404 against something
    # nobody typed.
    assert sport.needs({"teams": "X = futbol:notanumber"}, {}) == ()
    assert sport.needs({"teams": "X = futbol:CL"}, {})[0]["params"] == {
        "competition": "CL", "days": 30}


def test_the_same_team_twice_is_one_fetch():
    from homescreen.scenes import sport
    got = sport.needs({"teams": "A = futbol:86\nB = futbol:86"}, {})
    assert len(got) == 1


def test_the_old_single_team_still_works():
    from homescreen.scenes import sport
    got = sport.needs({"team": "86"}, {})
    assert len(got) == 1 and got[0]["provider"] == "football"


# --- merged and labelled ------------------------------------------------------

def _data(req):
    if req["provider"] == "nba":
        return Reading(data={"matches": [_fixtures.match(
            "2026-09-01T20:00:00+00:00", "LAL", "BOS", competition="NBA")]},
            ok=True, age_s=10.0)
    return Reading(data={"matches": [_fixtures.match(
        "2026-09-01T18:00:00+00:00", "Real Madrid", "Barcelona",
        competition="La Liga")]}, ok=True, age_s=10.0)


def test_fixtures_from_several_sports_merge_in_time_order():
    html = scenes.build("sport", _ctx(
        {"teams": "Madrid = futbol:86\nLakers = nba:LAL"}, _data)).html
    assert "Real Madrid" in html and "LAL" in html
    assert html.index("Real Madrid") < html.index("LAL"), "18:00 before 20:00"


def test_each_row_says_which_competition_it_is():
    # Not which FOLLOW it came from: a block following `Madrid = futbol:86`
    # labelled every row "MADRID" against "Real Madrid — Betis", which the row
    # already says. The competition is the thing the row does not tell you,
    # and it differs per fixture -- Real Madrid plays La Liga and the
    # Champions League in the same week.
    html = scenes.build("sport", _ctx(
        {"teams": "Madrid = futbol:86\nLakers = nba:LAL"}, _data)).html
    assert "La Liga" in html and "NBA" in html
    assert "Lakers" not in html, "the follow's name is not the label"


def test_a_block_of_one_competition_does_not_repeat_it_on_every_row():
    # The label is shown when it DISCRIMINATES, not when there is more than
    # one follow.
    def one_league(_req):
        return Reading(data={"matches": [
            _fixtures.match("2026-09-01T18:00:00+00:00", "Madrid", "Betis",
                            competition="La Liga"),
            _fixtures.match("2026-09-05T18:00:00+00:00", "Madrid", "Sevilla",
                            competition="La Liga")]}, ok=True, age_s=10.0)
    html = scenes.build("sport", _ctx(
        {"teams": "A = futbol:86\nB = futbol:81"}, one_league)).html
    assert 'class="src"' not in html


def test_a_long_official_name_is_shortened_for_the_row():
    from homescreen.scenes import sport
    assert sport.competition_name("Primera Division") == "La Liga"
    assert sport.competition_name("UEFA Champions League") == "Champions"
    assert sport.competition_name("EuroLeague") == "Euroliga"
    # Unknown still beats unlabelled, and UEFA distinguishes nothing.
    assert sport.competition_name("UEFA Nations League") == "Nations League"
    assert sport.competition_name("") == ""


def test_one_team_alone_is_not_labelled():
    html = scenes.build("sport", _ctx({"teams": "Madrid = futbol:86"},
                                      _data)).html
    assert 'class="src"' not in html


# --- the adapters -------------------------------------------------------------

def test_the_nba_header_set_is_complete():
    # `cdn.nba.com` answers 403 without these, and the failure looks exactly
    # like an outage. Verified 2026-09-01: the full set returns 192 KB, and
    # dropping the User-Agent or the encoding returns 403 and 431 bytes.
    # Tidying one away should fail a test rather than take the panel down.
    for header in ("User-Agent", "Accept-Encoding", "Accept-Language",
                   "Referer", "Sec-Fetch-Dest", "Sec-Fetch-Mode",
                   "Sec-Fetch-Site"):
        assert header in nba.HEADERS, header
    assert "gzip" in nba.HEADERS["Accept-Encoding"]
    assert "nba.com" in nba.HEADERS["Referer"]


def test_an_f1_race_reads_as_a_fixture():
    # A race has no home and away, so it is the event against its country --
    # which sits in the same row as "Real Madrid — Barcelona" without the
    # component knowing which sport it is looking at.
    body = {"MRData": {"RaceTable": {"Races": [
        {"raceName": "Italian Grand Prix", "date": "2026-09-06",
         "time": "13:00:00Z",
         "Circuit": {"Location": {"country": "Italy"}}}]}}}

    class Session:
        @staticmethod
        def get(url, **kw):
            class R:
                status_code = 200

                @staticmethod
                def json():
                    return body

                @staticmethod
                def raise_for_status():
                    pass
            return R()

    got = f1.fetch(f1.clean_params({}), session=Session())["matches"][0]
    assert got["home"] == "Italian Grand Prix" and got["away"] == "Italy"
    assert got["when"].endswith("+00:00"), "an instant needs its offset"
    assert got["competition"] == "F1"


@pytest.mark.parametrize("provider", [f1, nba])
def test_every_sport_source_emits_the_shared_shape(provider):
    made = _fixtures.match("2026-09-01T18:00:00+00:00", "A", "B")
    for field in _fixtures.REQUIRED:
        assert field in made, field
    assert made["status"] in (_fixtures.SCHEDULED, _fixtures.LIVE,
                              _fixtures.FINISHED)
    assert getattr(provider, "SECRETS") == (), "these were chosen for keyless"


# --- competitions, not only teams --------------------------------------------

def test_a_competition_is_a_source_in_its_own_right():
    # "Every Champions League tie" is what you want from a tournament you
    # follow but have no club in.
    from homescreen.scenes import sport
    got = sport.needs({"teams": "Champions = futbol:CL"}, {})
    assert got[0]["params"]["competition"] == "CL"
    assert "team" not in got[0]["params"]


def test_a_number_is_a_club_and_letters_are_a_competition():
    from homescreen.scenes import sport
    club = sport.needs({"teams": "futbol:86"}, {})[0]["params"]
    comp = sport.needs({"teams": "futbol:PD"}, {})[0]["params"]
    assert club["team"] == 86 and "competition" not in club
    assert comp["competition"] == "PD" and "team" not in comp


def test_euroleague_and_eurocup_are_the_same_source_configured_differently():
    from homescreen.scenes import sport
    el = sport.needs({"teams": "euroliga"}, {})[0]
    ec = sport.needs({"teams": "eurocup"}, {})[0]
    assert el["provider"] == ec["provider"] == "euroleague"
    assert el["params"]["competition"] == "E"
    assert ec["params"]["competition"] == "U"


def test_a_euroleague_team_narrows_the_same_competition():
    from homescreen.scenes import sport
    got = sport.needs({"teams": "Madrid = euroliga:MAD"}, {})[0]["params"]
    assert got == {"competition": "E", "team": "MAD", "days": 30}


def test_the_euroleague_season_is_derived_not_pinned():
    # A hardcoded season silently stops returning fixtures one summer and
    # looks exactly like the feed going away.
    import datetime
    from homescreen.fetch.providers import euroleague
    autumn = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)
    spring = datetime.datetime(2027, 3, 1, tzinfo=datetime.timezone.utc)
    assert euroleague._season("E", autumn) == "E2026"
    assert euroleague._season("E", spring) == "E2026", "a season spans new year"
    assert euroleague._season("U", autumn) == "U2026"


def test_euroleague_needs_no_key_and_no_xml():
    # Deferred once because the documented endpoint answers in XML, and
    # parsing untrusted XML with the standard library exposes entity
    # expansion on a box with no defusedxml. The v2 endpoint is JSON.
    from homescreen.fetch.providers import euroleague
    assert euroleague.SECRETS == ()
    assert "/v2/" in euroleague.ENDPOINT
