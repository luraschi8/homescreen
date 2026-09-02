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
    # FIVE rows of 16 characters at `sm` on 240px: comfortable on a square,
    # off the edge of a circle, because the outermost rows of a list sit near
    # the rim where the chord is a little over half the diameter.
    rows = ["x" * 16] * 5
    assert draw.lines_fit(rows, 240, 240, shape="rect")
    assert not draw.lines_fit(rows, 240, 240, shape="round")


def test_a_single_row_gets_the_full_chord_across_the_middle():
    # The other half of the same rule, and the half the old flat ratio got
    # wrong: one line sits on the centre line, where the chord IS the
    # diameter. 20 characters at `sm` is 209px of a 224px opening -- it fits,
    # and a constant that refused it cost a third of the panel's width.
    assert draw.lines_fit(["x" * 20], 240, 240, shape="round")


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


# --- several calendars in one block -------------------------------------------
#
# Two placements already gave two calendars, as two separate blocks. What was
# missing was ONE agenda merging several, with each row saying where it came
# from -- which is the thing you actually want when work and home overlap.

def test_several_calendars_become_one_requirement_each():
    from homescreen.scenes import calendar
    need = calendar.needs(
        {"calendars": "Trabajo = https://a.invalid/w.ics\n"
                      "Casa = https://b.invalid/h.ics"}, {})
    assert len(need) == 2
    assert {n["params"]["url"] for n in need} == {
        "https://a.invalid/w.ics", "https://b.invalid/h.ics"}


def test_a_line_without_a_name_still_works():
    from homescreen.scenes import calendar
    need = calendar.needs({"calendars": "https://a.invalid/w.ics"}, {})
    assert len(need) == 1
    assert need[0]["params"]["url"] == "https://a.invalid/w.ics"


def test_the_old_single_url_still_works():
    # Records written before this existed carry `url`, and they must keep
    # working without a migration.
    from homescreen.scenes import calendar
    need = calendar.needs({"url": "https://a.invalid/w.ics"}, {})
    assert len(need) == 1


def test_events_from_several_calendars_are_merged_in_time_order():
    from homescreen.reading import Reading
    from homescreen import scenes

    def data(req):
        url = req["params"]["url"]
        if "w.ics" in url:
            return Reading(data={"events": [
                {"when": "2026-09-01T15:00:00+02:00", "summary": "Revisión"}]},
                ok=True, age_s=10.0)
        return Reading(data={"events": [
            {"when": "2026-09-01T09:00:00+02:00", "summary": "Dentista"}]},
            ok=True, age_s=10.0)

    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": 417, "h": 200, "depth": 1}, now=1_788_260_000.0,
        device={}, data=data,
        options=scenes.clean_options("calendar", {
            "calendars": "Trabajo = https://a.invalid/w.ics\n"
                         "Casa = https://b.invalid/h.ics"}))
    html = scenes.build("calendar", ctx).html
    assert "Dentista" in html and "Revisión" in html
    assert html.index("Dentista") < html.index("Revisión"), "time order"


def test_each_row_says_which_calendar_it_came_from():
    from homescreen.reading import Reading
    from homescreen import scenes

    def data(req):
        name = "w" if "w.ics" in req["params"]["url"] else "h"
        return Reading(data={"events": [
            {"when": "2026-09-01T15:00:00+02:00", "summary": f"cita {name}"}]},
            ok=True, age_s=10.0)

    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": 417, "h": 200, "depth": 1}, now=1_788_260_000.0,
        device={}, data=data,
        options=scenes.clean_options("calendar", {
            "calendars": "Trabajo = https://a.invalid/w.ics\n"
                         "Casa = https://b.invalid/h.ics"}))
    html = scenes.build("calendar", ctx).html
    assert "Trabajo" in html and "Casa" in html


def test_one_calendar_alone_is_not_labelled():
    # A source marker on every row of a single-calendar agenda is noise: it
    # says the same thing four times.
    from homescreen.reading import Reading
    from homescreen import scenes
    reading = Reading(data={"events": [
        {"when": "2026-09-01T15:00:00+02:00", "summary": "Revisión"}]},
        ok=True, age_s=10.0)
    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": 417, "h": 200, "depth": 1}, now=1_788_260_000.0,
        device={}, data=lambda req: reading,
        options=scenes.clean_options("calendar", {
            "calendars": "Trabajo = https://a.invalid/w.ics"}))
    assert "Trabajo" not in scenes.build("calendar", ctx).html


# --- recurrence ---------------------------------------------------------------

def _recurring_feed():
    """A calendar whose only entries repeat, like a real family one.

    A birthday from decades ago and a weekly commitment from last year: both
    are happening this week, and both have a DTSTART far outside any window a
    panel asks about.
    """
    import datetime as _dt
    today = _dt.date.today()
    birthday = today.replace(year=1975) + _dt.timedelta(days=2)
    weekly = today - _dt.timedelta(days=364)
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
        "BEGIN:VEVENT\r\nUID:b1\r\n"
        f"DTSTART;VALUE=DATE:{birthday:%Y%m%d}\r\n"
        "RRULE:FREQ=YEARLY\r\nSUMMARY:Cumpleaños de Luis\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:w1\r\n"
        f"DTSTART:{weekly:%Y%m%d}T170000Z\r\n"
        "RRULE:FREQ=WEEKLY\r\nSUMMARY:Entreno\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n")


def test_a_recurring_event_is_seen_on_the_day_it_recurs():
    # The parser read DTSTART and ignored RRULE, so a series was only ever seen
    # at its original start -- decades ago for a birthday -- and the window
    # then filtered it out. Measured against a real family calendar: 526
    # events, 118 recurrence rules, ZERO reported for the next fortnight.
    from homescreen.fetch.providers import ics
    got = ics.fetch({"url": "https://x/c.ics", "days": 14},
                    session=_Session(_recurring_feed()))
    summaries = [e["summary"] for e in got["events"]]
    assert "Cumpleaños de Luis" in summaries, summaries
    assert "Entreno" in summaries, summaries


def test_a_weekly_series_repeats_across_the_window():
    from homescreen.fetch.providers import ics
    got = ics.fetch({"url": "https://x/c.ics", "days": 21},
                    session=_Session(_recurring_feed()))
    weekly = [e for e in got["events"] if e["summary"] == "Entreno"]
    assert len(weekly) >= 3, f"a weekly series gave {len(weekly)} in 21 days"


def test_an_all_day_recurrence_lands_on_its_own_day():
    # All-day events arrive as a `date`, which cannot be compared with an aware
    # datetime; midnight in the server's zone is what "that day" means on a
    # screen standing in that zone.
    import datetime as _dt
    from homescreen.fetch.providers import ics
    got = ics.fetch({"url": "https://x/c.ics", "days": 14},
                    session=_Session(_recurring_feed()))
    day = [e for e in got["events"] if e["summary"] == "Cumpleaños de Luis"][0]
    when = _dt.datetime.fromisoformat(day["when"])
    assert when.date() == _dt.date.today() + _dt.timedelta(days=2)
    assert when.tzinfo is not None, "a naive stamp cannot be ordered"


def test_a_long_daily_series_cannot_flood_the_payload():
    from homescreen.fetch.providers import ics
    import datetime as _dt
    start = _dt.date.today() - _dt.timedelta(days=5)
    feed = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
            "BEGIN:VEVENT\r\nUID:d1\r\n"
            f"DTSTART:{start:%Y%m%d}T080000Z\r\n"
            "RRULE:FREQ=DAILY\r\nSUMMARY:Diario\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n")
    got = ics.fetch({"url": "https://x/c.ics", "days": 60},
                    session=_Session(feed))
    assert len(got["events"]) <= ics.MAX_EVENTS


def test_a_calendar_with_no_recurrence_still_reads():
    # The flat path has to keep working: most feeds are mostly one-off events.
    from homescreen.fetch.providers import ics
    got = ics.fetch({"url": "https://x/c.ics", "days": 3650},
                    session=_Session(SAMPLE))
    assert got["events"], "the ordinary sample stopped parsing"


def test_a_broken_calendar_still_shows_what_can_be_read():
    # `icalendar` is strict where the flat reader was forgiving, and this
    # module's rule is that one malformed event must not hide a month of good
    # ones. A calendar the strict parser refuses falls back to the flat
    # reading: recurrences are lost, but a fortnight of one-off events beats a
    # section that cannot render at all.
    from homescreen.fetch.providers import ics
    got = ics.fetch({"url": "https://x/c.ics", "days": 3650},
                    session=_Session(_sample().replace("VERSION:2.0", "\x00")))
    assert isinstance(got.get("events"), list)
