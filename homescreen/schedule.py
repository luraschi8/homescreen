"""Which view a screen shows right now, and when that will next change.

A slot is a weekday set plus a wall-clock window. That is deliberately the
whole vocabulary: it expresses "radar during the day, clock at night, weather
on weekend mornings" and it cannot express dates, holidays, sunrise-relative
times or nth-weekday rules. Each of those is a real want and each would drag in
a calendar library, a recurrence grammar and a class of bug that only appears
in November.

Two decisions carry most of the weight:

**Membership testing, not edge triggering.** Everything here asks "which slot
contains this instant". Nothing asks "did a boundary just pass". That is what
makes daylight saving a non-event rather than a source of annual outages: when
the clocks skip 02:00->03:00, a boundary inside the skipped hour never needs to
fire, because nothing is waiting to fire. At the next evaluated instant the
slot is simply already active, or already over. When 02:00 happens twice, a
01:30-02:30 slot is active twice, which is harmless. A device that slept
through the transition asks "what now?" and gets a correct answer.

**The last matching slot wins.** Not the most specific, not the shortest, not
the first. One sentence a person can hold in their head, and the dashboard
shows the slots in order with the active one marked, so precedence is seen
rather than reasoned about.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: A slot's window is minutes-from-midnight, so all comparisons are integers.
MINUTES_PER_DAY = 24 * 60

#: Bounds on a stored schedule. Written from an unauthenticated LAN page, so
#: these are what stops a hand-edited file becoming an unbounded loop.
MAX_SLOTS = 64
MAX_VIEWS = 32


def _zone(name):
    """The schedule's timezone, or UTC. Never raises: a bad zone must not take
    down the route a panel depends on."""
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo("UTC")


def parse_hhmm(value) -> int | None:
    """"HH:MM" -> minutes from midnight. None if it is not a time."""
    try:
        hh, _, mm = str(value).partition(":")
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _days(raw) -> frozenset[int]:
    """ISO weekdays 1-7. Anything else is dropped rather than guessed at."""
    out = set()
    for day in raw or ():
        try:
            n = int(day)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 7:
            out.add(n)
    return frozenset(out)


def slot_contains(slot: dict, weekday: int, minute: int) -> bool:
    """Is this instant inside this slot?

    A window with `to <= from` wraps midnight -- the "clock from 23:00 to
    07:00" case -- and the wrapped tail belongs to the day the slot STARTED on,
    which is why the previous day is tested too. Getting that backwards makes a
    night slot end at midnight and the panel change while nobody is watching.
    """
    days = _days(slot.get("days"))
    if not days:
        return False
    start = parse_hhmm(slot.get("from"))
    end = parse_hhmm(slot.get("to"))
    if start is None or end is None:
        return False
    if start < end:
        return weekday in days and start <= minute < end
    if start == end:
        return weekday in days          # a full day, not an empty one
    # Wrapping: tonight's head, or the tail of a slot that began yesterday.
    yesterday = 7 if weekday == 1 else weekday - 1
    return ((weekday in days and minute >= start)
            or (yesterday in days and minute < end))


def active_view(schedule: dict, now: float, *, tz=None) -> str:
    """The name of the view showing at `now`. Never blank, never raises."""
    schedule = schedule if isinstance(schedule, dict) else {}
    default = schedule.get("default") or ""
    zone = _zone(tz or schedule.get("tz"))
    moment = datetime.fromtimestamp(now, zone)
    weekday, minute = moment.isoweekday(), moment.hour * 60 + moment.minute

    winner = default
    for slot in (schedule.get("slots") or ())[:MAX_SLOTS]:
        if isinstance(slot, dict) and slot_contains(slot, weekday, minute):
            winner = slot.get("view") or winner      # last match wins
    return winner


def seconds_to_next_change(schedule: dict, now: float, *, tz=None,
                           horizon_s: float = 86400.0) -> float | None:
    """How long until the showing view changes. None if it never does.

    Computed by WALKING boundaries and testing membership at each, rather than
    by arithmetic on the current slot: overlapping slots mean the next change is
    not necessarily this slot's end, and "last match wins" makes that genuinely
    hard to reason about in closed form. There are at most a few dozen slots and
    this runs once per poll, so the dull version is the right one.

    A mis-computation is bounded by design and not by care: `scenes.POLL_MAX_S`
    clamps any answer to ten minutes, so the worst case for a boundary this
    function gets wrong across a DST transition is one cycle late, never a
    stuck panel.
    """
    schedule = schedule if isinstance(schedule, dict) else {}
    slots = [s for s in (schedule.get("slots") or ())[:MAX_SLOTS]
             if isinstance(s, dict)]
    if not slots:
        return None

    zone = _zone(tz or schedule.get("tz"))
    start = datetime.fromtimestamp(now, zone)
    showing = active_view(schedule, now, tz=tz)

    # Every wall-clock minute any slot could turn over, as minutes-from-midnight.
    edges = set()
    for slot in slots:
        for key in ("from", "to"):
            minute = parse_hhmm(slot.get(key))
            if minute is not None:
                edges.add(minute)
    if not edges:
        return None

    # Today's remaining edges, then each following day, until the horizon.
    for day_offset in range(0, int(horizon_s // 86400) + 2):
        midnight = (start + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for minute in sorted(edges):
            when = midnight + timedelta(minutes=minute)
            delta = when.timestamp() - now
            if delta <= 0:
                continue
            if delta > horizon_s:
                return None
            if active_view(schedule, when.timestamp(), tz=tz) != showing:
                return delta
    return None


def problems(raw, known_views) -> list:
    """What a POSTED schedule is malformed about, in Spanish, for the API.

    `clean_schedule` is lenient by necessity -- it also reads a stored file
    that may have been edited by hand, and a bad record must never take the
    daemon down. But a PUT is somebody asking for something, and quietly
    keeping two thirds of it is how a night slot comes to cover six days out
    of seven and nobody finds out until Sunday.

    Reports rather than raises: the caller decides whether a partial schedule
    is a 400 or a warning.
    """
    raw = raw if isinstance(raw, dict) else {}
    views = set(known_views or ())
    out = []
    for index, slot in enumerate((raw.get("slots") or ()), start=1):
        if not isinstance(slot, dict):
            out.append(f"franja {index}: no es un objeto")
            continue
        # NOT referential integrity. The views editor posts the whole
        # arrangement, so a slot pointing at a view this same request removed
        # must vanish quietly -- refusing it would block a legitimate edit.
        # What is reported here is data this endpoint cannot read at all.
        for field in ("from", "to"):
            if parse_hhmm(slot.get(field)) is None:
                out.append(f"franja {index}: «{field}» no es una hora HH:MM")
        given = list(slot.get("days") or ())
        kept = _days(given)
        if not kept:
            out.append(f"franja {index}: sin días válidos "
                       f"(1 = lunes … 7 = domingo)")
        elif len(kept) != len({str(d) for d in given}):
            # The likely mistake by far: JavaScript's getDay() is 0 = Sunday,
            # so a client that speaks it loses a day and is told nothing.
            dropped = sorted({str(d) for d in given}
                             - {str(d) for d in sorted(kept)})
            out.append(f"franja {index}: días fuera de rango "
                       f"({', '.join(dropped)}); 1 = lunes … 7 = domingo")
    return out


def clean_schedule(raw, known_views) -> dict:
    """Coerce a stored or posted schedule. Never raises.

    Slots naming a view that does not exist are DROPPED rather than kept: a
    slot pointing at nothing would silently fall through to the default, which
    looks identical to the slot not matching, and that is the kind of thing
    someone debugs for an hour.
    """
    raw = raw if isinstance(raw, dict) else {}
    views = set(known_views or ())
    default = raw.get("default")
    if default not in views:
        default = next(iter(sorted(views)), "")

    slots = []
    for slot in (raw.get("slots") or ())[:MAX_SLOTS]:
        if not isinstance(slot, dict):
            continue
        if slot.get("view") not in views:
            continue
        if parse_hhmm(slot.get("from")) is None:
            continue
        if parse_hhmm(slot.get("to")) is None:
            continue
        days = sorted(_days(slot.get("days")))
        if not days:
            continue
        slots.append({"view": str(slot["view"]), "days": days,
                      "from": str(slot["from"]), "to": str(slot["to"])})
    out = {"default": default, "slots": slots}
    if raw.get("tz"):
        out["tz"] = str(raw["tz"])
    return out
