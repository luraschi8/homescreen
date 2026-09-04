"""Which view is showing, and when it next changes.

The DST tests are the point of this file. A scheduler that edge-triggers is
correct for 363 days a year and wrong on the two that involve an argument with
a customer, so these pin the behaviour at both Madrid transitions rather than
trusting that membership testing works out.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from homescreen import schedule

MADRID = ZoneInfo("Europe/Madrid")

DAY = {"view": "dia", "days": [1, 2, 3, 4, 5, 6, 7], "from": "09:00", "to": "23:00"}
NIGHT = {"view": "noche", "days": [1, 2, 3, 4, 5, 6, 7], "from": "23:00", "to": "07:00"}
WEEKEND = {"view": "finde", "days": [6, 7], "from": "08:00", "to": "12:00"}
S = {"tz": "Europe/Madrid", "default": "reposo",
     "slots": [DAY, NIGHT, WEEKEND]}


def at(stamp: str) -> float:
    return datetime.fromisoformat(stamp).replace(tzinfo=MADRID).timestamp()


# --- membership -------------------------------------------------------------

@pytest.mark.parametrize("when,expected", [
    ("2026-08-27T10:00", "dia"),        # Thursday mid-morning
    ("2026-08-27T22:59", "dia"),        # the last minute before the flip
    ("2026-08-27T23:00", "noche"),      # the flip itself
    ("2026-08-28T03:00", "noche"),      # the wrapped tail, on the NEXT day
    ("2026-08-28T06:59", "noche"),
    ("2026-08-28T07:00", "reposo"),     # the gap
    ("2026-08-28T08:30", "reposo"),     # weekday: no weekend slot
    ("2026-08-29T08:30", "finde"),      # Saturday: there is one
])
def test_the_showing_view(when, expected):
    assert schedule.active_view(S, at(when)) == expected


def test_a_wrapped_slot_belongs_to_the_day_it_started_on():
    # A night slot listed for Monday only must cover Tuesday's small hours --
    # getting this backwards ends the slot at midnight and the panel changes
    # while nobody is watching.
    monday_night = {"view": "noche", "days": [1], "from": "23:00", "to": "07:00"}
    s = {"default": "d", "slots": [monday_night], "tz": "Europe/Madrid"}
    assert schedule.active_view(s, at("2026-08-31T23:30")) == "noche"   # Mon
    assert schedule.active_view(s, at("2026-09-01T03:00")) == "noche"   # Tue am
    assert schedule.active_view(s, at("2026-09-01T23:30")) == "d", "not Tue pm"


def test_the_last_matching_slot_wins():
    # Saturday 08:30 matches nothing else; Saturday 09:30 matches both `dia`
    # and `finde`, and `finde` is later in the list.
    assert schedule.active_view(S, at("2026-08-29T09:30")) == "finde"
    reordered = {**S, "slots": [WEEKEND, DAY, NIGHT]}
    assert schedule.active_view(reordered, at("2026-08-29T09:30")) == "dia"


def test_no_match_falls_back_to_the_default_never_to_blank():
    # A screen showing nothing looks broken and cannot be told from a dead one.
    assert schedule.active_view(S, at("2026-08-28T07:30")) == "reposo"
    assert schedule.active_view({"default": "x", "slots": []}, at("2026-08-28T07:30")) == "x"


def test_a_window_that_starts_where_it_ends_is_a_whole_day():
    s = {"default": "d", "slots": [{"view": "todo", "days": [4],
                                    "from": "12:00", "to": "12:00"}]}
    assert schedule.active_view(s, at("2026-08-27T03:00")) == "todo"
    assert schedule.active_view(s, at("2026-08-27T18:00")) == "todo"


# --- daylight saving --------------------------------------------------------

def test_spring_forward_does_not_strand_a_slot_in_the_hour_that_never_happens():
    # Madrid 2026-03-29: 02:00 -> 03:00. A slot boundary at 02:30 is inside an
    # hour that does not exist. Edge triggering would wait for a moment that
    # never arrives; membership testing simply finds the slot already over.
    s = {"tz": "Europe/Madrid", "default": "noche",
         "slots": [{"view": "madrugada", "days": [1, 2, 3, 4, 5, 6, 7],
                    "from": "01:00", "to": "02:30"}]}
    before = datetime(2026, 3, 29, 1, 30, tzinfo=MADRID).timestamp()
    after = datetime(2026, 3, 29, 3, 30, tzinfo=MADRID).timestamp()
    assert schedule.active_view(s, before) == "madrugada"
    assert schedule.active_view(s, after) == "noche", "already over, not stuck"


def test_fall_back_lets_a_slot_be_active_twice_which_is_harmless():
    # Madrid 2026-10-25: 03:00 -> 02:00, so 02:30 happens twice.
    s = {"tz": "Europe/Madrid", "default": "d",
         "slots": [{"view": "repetida", "days": [1, 2, 3, 4, 5, 6, 7],
                    "from": "02:00", "to": "03:00"}]}
    first = datetime(2026, 10, 25, 2, 30, tzinfo=MADRID, fold=0).timestamp()
    second = datetime(2026, 10, 25, 2, 30, tzinfo=MADRID, fold=1).timestamp()
    assert first != second, "the two 02:30s are distinct instants"
    assert schedule.active_view(s, first) == "repetida"
    assert schedule.active_view(s, second) == "repetida"


def test_a_device_that_slept_through_a_transition_still_gets_a_straight_answer():
    # The property that matters for a panel polling every ten minutes.
    s = {"tz": "Europe/Madrid", "default": "noche",
         "slots": [{"view": "dia", "days": [1, 2, 3, 4, 5, 6, 7],
                    "from": "07:00", "to": "23:00"}]}
    assert schedule.active_view(s, datetime(2026, 3, 29, 9, 0,
                                            tzinfo=MADRID).timestamp()) == "dia"


# --- when does it next change ----------------------------------------------

@pytest.mark.parametrize("when,hours", [
    ("2026-08-27T10:00", 13.0),         # -> 23:00
    ("2026-08-27T23:30", 7.5),          # -> 07:00
    ("2026-08-28T07:30", 1.5),          # -> 09:00
])
def test_the_next_change_is_the_next_time_the_answer_differs(when, hours):
    got = schedule.seconds_to_next_change(S, at(when))
    assert got == pytest.approx(hours * 3600, abs=1.0)


def test_a_schedule_with_no_slots_never_changes():
    assert schedule.seconds_to_next_change({"default": "d", "slots": []},
                                           at("2026-08-27T10:00")) is None


def test_a_boundary_that_does_not_change_the_answer_is_not_a_change():
    # Two back-to-back slots on the same view: the seam is not a change, and
    # waking a panel for it would be a frame nobody can see.
    s = {"tz": "Europe/Madrid", "default": "d", "slots": [
        {"view": "x", "days": [4], "from": "09:00", "to": "12:00"},
        {"view": "x", "days": [4], "from": "12:00", "to": "15:00"}]}
    got = schedule.seconds_to_next_change(s, at("2026-08-27T10:00"))
    assert got == pytest.approx(5 * 3600, abs=1.0), "15:00, not 12:00"


def test_the_next_change_is_found_across_a_day_boundary():
    s = {"tz": "Europe/Madrid", "default": "d", "slots": [
        {"view": "finde", "days": [6], "from": "08:00", "to": "12:00"}]}
    got = schedule.seconds_to_next_change(s, at("2026-08-28T20:00"))  # Friday
    assert got == pytest.approx(12 * 3600, abs=1.0)                   # Sat 08:00


# --- nothing here may raise -------------------------------------------------

@pytest.mark.parametrize("bad", [
    None, {}, [], "nonsense", 5,
    {"slots": "not a list"},
    {"slots": [None, 5, "x"]},
    {"slots": [{"view": "v", "days": "17", "from": "09:00", "to": "10:00"}]},
    {"slots": [{"view": "v", "days": [1], "from": "25:00", "to": "10:00"}]},
    {"slots": [{"view": "v", "days": [1], "from": "9", "to": "10:00"}]},
    {"slots": [{"view": "v", "days": [99], "from": "09:00", "to": "10:00"}]},
    {"tz": "Mars/Olympus", "slots": [DAY]},
])
def test_a_malformed_schedule_answers_rather_than_exploding(bad):
    # This runs on the route a panel depends on, and the file behind it is
    # written from an unauthenticated page.
    assert isinstance(schedule.active_view(bad, at("2026-08-27T10:00")), str)
    schedule.seconds_to_next_change(bad, at("2026-08-27T10:00"))


def test_an_unknown_timezone_falls_back_rather_than_failing():
    s = {"tz": "Mars/Olympus", "default": "d",
         "slots": [{"view": "v", "days": [1, 2, 3, 4, 5, 6, 7],
                    "from": "00:00", "to": "23:59"}]}
    assert schedule.active_view(s, at("2026-08-27T10:00")) == "v"


# --- validation -------------------------------------------------------------

def test_a_slot_pointing_at_a_view_that_does_not_exist_is_dropped():
    # Kept, it would silently fall through to the default -- which looks
    # exactly like the slot not matching, and is an hour of debugging.
    cleaned = schedule.clean_schedule(
        {"default": "a", "slots": [DAY, {"view": "ghost", "days": [1],
                                         "from": "01:00", "to": "02:00"}]},
        known_views={"a", "dia"})
    assert [s["view"] for s in cleaned["slots"]] == ["dia"]


def test_a_default_naming_nothing_is_replaced_rather_than_left_dangling():
    cleaned = schedule.clean_schedule({"default": "ghost", "slots": []},
                                      known_views={"a", "b"})
    assert cleaned["default"] in {"a", "b"}


def test_the_slot_list_is_bounded():
    many = [dict(DAY) for _ in range(500)]
    cleaned = schedule.clean_schedule({"default": "dia", "slots": many},
                                      known_views={"dia"})
    assert len(cleaned["slots"]) <= schedule.MAX_SLOTS


# --- the API says what it refused --------------------------------------------
#
# `clean_schedule` is lenient by necessity: it also reads stored data, and a
# record that has been hand-edited must never take the daemon down. But a PUT
# is somebody asking for something, and silently keeping two thirds of it is
# how a night slot comes to cover six days out of seven.

def test_a_weekday_outside_one_to_seven_is_reported_not_dropped():
    problems = schedule.problems(
        {"default": "d", "slots": [
            {"view": "n", "days": [0, 1, 2], "from": "23:00", "to": "07:00"}]},
        {"d", "n"})
    assert problems, "day 0 is not a weekday here"
    assert any("1" in p and "7" in p for p in problems), problems


def test_the_zero_based_mistake_is_named_because_it_is_the_likely_one():
    # JavaScript's getDay() is 0=Sunday, so this is the trap somebody actually
    # falls into -- including whoever wrote this test the first time.
    problems = schedule.problems(
        {"default": "d", "slots": [
            {"view": "n", "days": [0], "from": "23:00", "to": "07:00"}]},
        {"d", "n"})
    assert any("lunes" in p for p in problems), problems


def test_a_slot_naming_an_unknown_view_is_dropped_quietly_not_reported():
    # Deliberately NOT an error. The views editor posts the whole arrangement
    # at once, so emptying a view and leaving a slot pointing at it is one
    # edit, not a mistake -- refusing it would block the edit.
    assert schedule.problems(
        {"default": "d", "slots": [
            {"view": "gone", "days": [1], "from": "08:00", "to": "09:00"}]},
        {"d"}) == []


def test_an_unreadable_time_is_reported():
    problems = schedule.problems(
        {"default": "d", "slots": [
            {"view": "d", "days": [1], "from": "25:99", "to": "09:00"}]},
        {"d"})
    assert problems


def test_a_schedule_that_survives_whole_reports_nothing():
    assert schedule.problems(
        {"default": "d", "slots": [
            {"view": "d", "days": [1, 7], "from": "23:00", "to": "07:00"}]},
        {"d"}) == []


def test_stored_data_is_still_read_leniently():
    # The other half: `clean_schedule` must go on dropping quietly, because it
    # also reads a file that may have been edited by hand.
    got = schedule.clean_schedule(
        {"default": "d", "slots": [
            {"view": "d", "days": [0, 1], "from": "23:00", "to": "07:00"}]},
        {"d"})
    assert got["slots"][0]["days"] == [1]


# --- rotations ----------------------------------------------------------------

ALL_DAYS = [1, 2, 3, 4, 5, 6, 7]


def _rotation(views=("a", "b", "c"), every=20, frm="09:00", to="23:00"):
    return {"kind": "rotation", "views": list(views), "every_minutes": every,
            "days": ALL_DAYS, "from": frm, "to": to}


def _at(hhmm, day=4):
    """(weekday, minutes-from-midnight)."""
    h, m = (int(x) for x in hhmm.split(":"))
    return day, h * 60 + m


def test_a_rule_with_no_kind_is_the_original_slot():
    # Today's devices.json must load unchanged.
    assert schedule.kind_of({"view": "a"}) == "fixed"
    assert schedule.kind_of({"kind": "rotation"}) == "rotation"
    assert schedule.kind_of("nonsense") == "fixed"


def test_a_rotation_turns_over_on_its_own_interval():
    rule = _rotation()
    assert schedule.turn_at(rule, *_at("09:00")) == "a"
    assert schedule.turn_at(rule, *_at("09:19")) == "a"
    assert schedule.turn_at(rule, *_at("09:20")) == "b"
    assert schedule.turn_at(rule, *_at("09:40")) == "c"
    assert schedule.turn_at(rule, *_at("10:00")) == "a", "it comes round again"


def test_a_rotation_is_anchored_to_its_own_window_not_to_midnight():
    # A rotation starting at 09:10 shows its FIRST view at 09:10, whatever the
    # interval divides into.
    rule = _rotation(frm="09:10")
    assert schedule.turn_at(rule, *_at("09:10")) == "a"
    assert schedule.turn_at(rule, *_at("09:30")) == "b"


def test_a_rotation_across_midnight_keeps_counting():
    rule = _rotation(frm="23:00", to="01:00", every=20)
    assert schedule.turn_at(rule, *_at("23:00")) == "a"
    assert schedule.turn_at(rule, *_at("23:40")) == "c"
    assert schedule.turn_at(rule, *_at("00:00")) == "a"
    assert schedule.turn_at(rule, *_at("00:20")) == "b"


def test_the_showing_view_needs_no_stored_cursor():
    # Derived from the wall clock, so a panel that reboots mid-cycle lands on
    # the view it would have reached rather than resuming a lost position.
    rule = _rotation()
    first = schedule.turn_at(rule, *_at("14:25"))
    again = schedule.turn_at(rule, *_at("14:25"))
    assert first == again == schedule.turn_at(rule, *_at("14:39"))


def test_a_rotation_drives_active_view():
    plan = {"default": "off", "tz": "Europe/Madrid", "slots": [_rotation()]}
    import datetime, zoneinfo
    mad = zoneinfo.ZoneInfo("Europe/Madrid")
    def at(hhmm):
        h, m = (int(x) for x in hhmm.split(":"))
        return schedule.active_view(
            plan, datetime.datetime(2026, 9, 4, h, m, tzinfo=mad).timestamp())
    assert at("08:59") == "off"
    assert at("09:00") == "a"
    assert at("09:20") == "b"
    assert at("23:00") == "off"


def test_the_panel_wakes_for_every_turn_not_just_the_window():
    # Without the ticks as boundaries a screen would sleep through its own
    # changes and only wake when the window closed.
    import datetime, zoneinfo
    mad = zoneinfo.ZoneInfo("Europe/Madrid")
    plan = {"default": "off", "tz": "Europe/Madrid", "slots": [_rotation()]}
    now = datetime.datetime(2026, 9, 4, 9, 5, tzinfo=mad).timestamp()
    secs = schedule.seconds_to_next_change(plan, now)
    assert secs is not None and 14 * 60 <= secs <= 15 * 60, secs


def test_a_rotation_survives_cleaning_and_keeps_its_shape():
    plan = schedule.clean_schedule(
        {"default": "a", "slots": [_rotation()]}, {"a", "b", "c"})
    kept = plan["slots"][0]
    assert kept["kind"] == "rotation"
    assert kept["views"] == ["a", "b", "c"]
    assert kept["every_minutes"] == 20


def test_a_rotation_whose_views_vanish_degrades_rather_than_disappearing():
    # A rule that VANISHES falls through to the default, which looks exactly
    # like a rule that did not match -- an hour of somebody's evening.
    plan = schedule.clean_schedule(
        {"default": "a", "slots": [_rotation()]}, {"a"})
    assert len(plan["slots"]) == 1
    assert plan["slots"][0].get("kind") is None, "it became a fixed rule"
    assert plan["slots"][0]["view"] == "a"


def test_a_rotation_with_no_surviving_view_is_dropped():
    plan = schedule.clean_schedule(
        {"default": "z", "slots": [_rotation()]}, {"z"})
    assert plan["slots"] == []


def test_an_absurd_interval_is_clamped_rather_than_dividing_by_zero():
    for bad in (0, -5, None, "x", 99999):
        rule = _rotation(every=bad)
        assert schedule.MIN_ROTATION_MINUTES <= schedule.rotation_minutes(rule) \
            <= schedule.MAX_ROTATION_MINUTES
        assert schedule.turn_at(rule, *_at("12:00")) in ("a", "b", "c")


def test_problems_names_what_a_rotation_got_wrong():
    said = schedule.problems(
        {"slots": [_rotation(views=("a",))]}, {"a", "b"})
    assert any("al menos" in m for m in said), said
    said = schedule.problems(
        {"slots": [_rotation(views=("a", "zzz"))]}, {"a", "b"})
    assert any("zzz" in m for m in said), said
    said = schedule.problems({"slots": [_rotation(every=0)]}, {"a", "b", "c"})
    assert any("intervalo" in m for m in said), said


def test_too_many_rules_is_refused_aloud_rather_than_sliced():
    # `clean_schedule` slices to the cap, so without this the 65th rule is
    # discarded with nothing said -- and a schedule that silently lost its
    # night rule looks exactly like one that never had it.
    many = [{"view": "a", "days": ALL_DAYS, "from": "00:00", "to": "01:00"}
            for _ in range(schedule.MAX_SLOTS + 1)]
    said = schedule.problems({"slots": many}, {"a"})
    assert any(str(schedule.MAX_SLOTS) in m for m in said), said
