"""What is coming up, and how much of it fits."""
import datetime
import pathlib
import tempfile

import pytest

from homescreen import draw, scenes
from homescreen.reading import Reading

NOW = datetime.datetime.fromisoformat("2026-08-28T10:00:00+02:00").timestamp()
EVENTS = [
    {"when": "2026-08-28T14:00:00+02:00", "summary": "Dentista"},
    {"when": "2026-08-29T09:00:00+02:00", "summary": "Reunión de producto"},
    {"when": "2026-09-04T19:30:00+02:00", "summary": "Cena con Ana"},
]
EPAPER = {"w": 800, "h": 480, "depth": 1}
ROUND = {"w": 240, "h": 240, "depth": 16, "shape": "round"}


def drawn(caps, events=EVENTS, options=None, now=NOW):
    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=caps, now=now,
        device={}, options={"url": "https://x/c.ics", **(options or {})},
        data=lambda req: Reading(data={"events": events}, ok=True))
    return [d["v"] for d in scenes.build("calendar", ctx).components[0]["draw"]]


def test_a_wide_panel_lists_what_is_coming():
    values = drawn(EPAPER)
    assert len(values) == 3
    assert "Dentista" in values[0]


def test_a_small_round_panel_shows_the_next_one():
    # Which is the answer you ask a small screen.
    values = drawn(ROUND)
    assert "Dentista" in values
    assert "Cena con Ana" not in " ".join(values)
    assert "+2 mas" in values, "and says there is more"


def test_times_are_written_the_way_a_person_reads_them():
    values = " ".join(drawn(EPAPER))
    assert "hoy 14:00" in values
    assert "manana 09:00" in values, "the font has no ñ"
    assert "4 sep 19:30" in values, "past this week, a date"


def test_with_no_calendar_configured_it_says_what_to_do():
    values = drawn(ROUND, options={"url": ""})
    assert any("sin calendario" in v for v in values)
    assert any(".ics" in v for v in values)


def test_an_empty_calendar_is_not_a_broken_one():
    values = drawn(ROUND, events=[])
    assert any("nada a la vista" in v for v in values)


def test_a_calendar_with_no_url_asks_for_no_fetch():
    assert scenes.needs("calendar", {"url": ""}, {}) == ()
    assert len(scenes.needs("calendar", {"url": "https://x/c.ics"}, {})) == 1


def test_two_screens_on_two_calendars_are_two_fetches():
    from homescreen import fetch
    plan = fetch.derive(
        {"a": {"scene": "calendar", "options": {"url": "https://x/a.ics"}},
         "b": {"scene": "calendar", "options": {"url": "https://x/b.ics"}}}, {})
    assert len(plan) == 2


# --- the shared fitting rule ------------------------------------------------

def test_the_fitting_rule_measures_the_longest_line():
    short = ["AAPL 227.40", "MSFT 415.20"]
    long = ["BINANCE:BTCUSDT  63,120   ▲ 2.90%", "MSFT 415.20"]
    assert draw.lines_fit(short, 240, 240, shape="round")
    assert not draw.lines_fit(long, 240, 240, shape="round")
    assert draw.lines_fit(long, 800, 480), "the same lines fit a wide panel"


def test_a_circle_has_less_usable_width_than_its_diameter():
    # 20 characters at `sm` on 240px: comfortable across a square, off the
    # edge of a circle, because the rows a list occupies sit where the chord
    # is well short of the diameter.
    line = ["x" * 20]
    assert draw.lines_fit(line, 240, 240, shape="rect")
    assert not draw.lines_fit(line, 240, 240, shape="round")


def test_more_rows_than_the_vocabulary_has_never_fit():
    assert not draw.lines_fit(["a"] * 6, 800, 480)


def test_rows_that_would_touch_do_not_fit():
    assert not draw.lines_fit(["a", "b", "c", "d", "e"], 800, 60)


# --- the provider -----------------------------------------------------------

def _sample(days_ahead=(1, 2)):
    """An ICS feed whose events are always upcoming.

    The first version hardcoded 28 and 29 August. They were upcoming the day
    it was written and in the past three days later, so the provider filtered
    them out and two tests started failing for a reason that had nothing to do
    with the code. A fixture that expires is a fixture that lies about when it
    broke.
    """
    soon = [(datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(days=d)).strftime("%Y%m%dT%H%M%SZ")
            for d in days_ahead]
    return f"""BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:{soon[0]}
SUMMARY:Dentista
END:VEVENT
BEGIN:VEVENT
DTSTART:{soon[1]}
SUMMARY:Reunión con el equipo de
  producto
END:VEVENT
BEGIN:VEVENT
DTSTART:20200101T090000Z
SUMMARY:Hace años
END:VEVENT
END:VCALENDAR"""


SAMPLE = _sample()


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, text):
        self._text = text

    def get(self, *a, **k):
        return _Resp(self._text)


def test_folded_lines_are_rejoined():
    # ICS wraps long lines with a leading space; a parser that misses it
    # truncates every long summary.
    from homescreen.fetch.providers import ics
    got = ics.fetch({"url": "https://x/c.ics", "days": 3650},
                    session=_Session(SAMPLE))
    summaries = [e["summary"] for e in got["events"]]
    assert "Reunión con el equipo de producto" in summaries


def test_events_long_past_are_not_upcoming():
    from homescreen.fetch.providers import ics
    got = ics.fetch({"url": "https://x/c.ics", "days": 3650},
                    session=_Session(SAMPLE))
    assert "Hace años" not in [e["summary"] for e in got["events"]]


def test_a_login_page_is_not_an_empty_calendar():
    # It is still a 200 and still text. "Nothing coming up" is a lie a person
    # acts on -- they miss the dentist.
    from homescreen.fetch.providers import ics
    with pytest.raises(ValueError, match="iCalendar"):
        ics.fetch({"url": "https://x/c.ics"},
                  session=_Session("<html>Sign in</html>"))


def test_one_malformed_event_does_not_hide_the_others():
    from homescreen.fetch.providers import ics
    sample = _sample()
    first = sample.split("DTSTART:")[1].split("\n")[0]
    broken = sample.replace(f"DTSTART:{first}", "DTSTART:nonsense")
    got = ics.fetch({"url": "https://x/c.ics", "days": 3650},
                    session=_Session(broken))
    assert any("producto" in e["summary"] for e in got["events"])


@pytest.mark.parametrize("bad", ["", "ftp://x/c.ics", "javascript:x", "x" * 600])
def test_a_url_that_is_not_a_calendar_url_is_refused(bad):
    from homescreen import fetch
    with pytest.raises(ValueError):
        fetch.providers.clean_params("ics", {"url": bad})


def test_webcal_is_accepted_and_fetched_over_https():
    # Two halves, and only the first was tested: the URL is STORED as the
    # operator typed it, and REWRITTEN on the way out. Asserting the first on
    # the string the test itself passed in proved nothing -- the rewrite could
    # be deleted and every webcal calendar would fail with an unknown-scheme
    # error that no test would show.
    from homescreen import fetch
    params = fetch.providers.clean_params("ics", {"url": "webcal://x/c.ics"})
    assert params["url"].startswith("webcal://"), "stored as written"

    asked = {}

    class Session:
        @staticmethod
        def get(url, **kw):
            asked["url"] = url
            raise RuntimeError("far enough -- the scheme is what is on trial")

    with pytest.raises(RuntimeError):
        fetch.providers.ics.fetch(params, session=Session())
    assert asked["url"] == "https://x/c.ics", "fetched over https"
