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
    assert sport.needs({"teams": "X = futbol:notanumber"}, {}) == ()


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


def test_each_row_says_which_team_it_belongs_to():
    html = scenes.build("sport", _ctx(
        {"teams": "Madrid = futbol:86\nLakers = nba:LAL"}, _data)).html
    assert "Madrid" in html and "Lakers" in html


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
