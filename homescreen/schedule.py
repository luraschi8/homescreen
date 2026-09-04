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

**A rule is fixed or a rotation.** A fixed rule is the original slot and is
byte-identical on disk: no `kind` means `fixed`. A rotation names several views
and an interval, and answers `views[(elapsed // every) % len(views)]` where
`elapsed` is minutes since its own window opened. That second kind exists
because "alternate between these three every twenty minutes from nine to
eleven" is one sentence and one intention, and expressing it as forty-two
fixed rules is not merely verbose -- it is LOSSY. Nothing on disk then records
that those rows were one rule, so no editor can show it as one, and changing
the interval means regenerating every row.

The rotation is derived from the wall clock with no stored cursor, which is
what keeps the membership-testing property above: a panel that reboots
mid-cycle, polls late, or sleeps through a DST transition asks "what now?" and
gets the right answer rather than resuming a position it no longer has.

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

#: What a rotation may ask for. An interval under a minute is not a schedule,
#: and half a day between turns is already a fixed rule with extra steps.
MIN_ROTATION_MINUTES = 1
MAX_ROTATION_MINUTES = 720
#: Two is the smallest thing that rotates; past eight nobody can predict what
#: the screen will show, which is the point of a rotation.
MIN_ROTATION_VIEWS = 2
MAX_ROTATION_VIEWS = 8

#: The view every screen has and none stores: a screen showing nothing.
#:
#: Going dark used to mean keeping a view that held `blank` with `tone: off` --
#: a DEVICE STATE dressed as content, which cost a slot in every picker and had
#: to be created before a screen could be told to turn off at night.
OFF_VIEW = "apagado"


def kind_of(rule) -> str:
    """`"rotation"` or `"fixed"`. A rule with no kind is the original slot."""
    if isinstance(rule, dict) and str(rule.get("kind") or "") == "rotation":
        return "rotation"
    return "fixed"


def rotation_views(rule) -> list:
    """The view names a rotation cycles through, in order."""
    raw = rule.get("views") if isinstance(rule, dict) else None
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(v) for v in raw[:MAX_ROTATION_VIEWS] if str(v or "").strip()]


def rotation_minutes(rule) -> int:
    """A rotation's interval, clamped. Never zero: that would divide by it."""
    try:
        every = int(float((rule or {}).get("every_minutes")))
    except (TypeError, ValueError):
        every = 0
    return max(MIN_ROTATION_MINUTES, min(MAX_ROTATION_MINUTES, every or 1))


def turn_at(rule, weekday: int, minute: int) -> str:
    """Which of a rotation's views is showing at this instant, or "".

    Anchored to the rule's OWN window rather than to midnight, so a rotation
    that starts at 09:00 begins on its first view at 09:00 whatever the
    interval divides into. Derived from the wall clock with no stored cursor:
    the answer after a reboot is the answer the panel would have reached.
    """
    views = rotation_views(rule)
    if not views:
        return ""
    start = parse_hhmm(rule.get("from"))
    if start is None:
        return ""
    elapsed = minute - start
    if elapsed < 0:
        # The tail of a window that opened yesterday.
        elapsed += MINUTES_PER_DAY
    return views[(elapsed // rotation_minutes(rule)) % len(views)]


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
        if not isinstance(slot, dict) or not slot_contains(slot, weekday, minute):
            continue
        if kind_of(slot) == "rotation":
            winner = turn_at(slot, weekday, minute) or winner
        else:
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
        # A rotation turns over between its own edges, so every tick is a
        # boundary too -- otherwise the panel would sleep through its own
        # changes and only wake when the window closed.
        if kind_of(slot) == "rotation":
            # `opens`/`closes`, not `start`/`end`: `start` is the datetime this
            # walk begins from, and shadowing it here made the boundary walk
            # add a timedelta to an int.
            opens = parse_hhmm(slot.get("from"))
            closes = parse_hhmm(slot.get("to"))
            if opens is None or closes is None:
                continue
            span = (closes - opens) % MINUTES_PER_DAY or MINUTES_PER_DAY
            step = rotation_minutes(slot)
            for tick in range(step, span, step):
                edges.add((opens + tick) % MINUTES_PER_DAY)
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
    asked = list(raw.get("slots") or ())
    if len(asked) > MAX_SLOTS:
        # `clean_schedule` slices to the cap, so without this the 65th rule is
        # discarded with nothing said -- and a schedule that silently lost its
        # night rule looks exactly like one that never had it.
        out.append(f"el horario admite {MAX_SLOTS} reglas y se han enviado "
                   f"{len(asked)}")
    for index, slot in enumerate(asked, start=1):
        if not isinstance(slot, dict):
            out.append(f"franja {index}: no es un objeto")
            continue
        # NOT referential integrity. The views editor posts the whole
        # arrangement, so a slot pointing at a view this same request removed
        # must vanish quietly -- refusing it would block a legitimate edit.
        # What is reported here is data this endpoint cannot read at all.
        for field in ("from", "to"):
            if parse_hhmm(slot.get(field)) is None:
                out.append(f"regla {index}: «{field}» no es una hora HH:MM")
        if kind_of(slot) == "rotation":
            names = rotation_views(slot)
            if len(names) < MIN_ROTATION_VIEWS:
                out.append(f"regla {index}: una rotación necesita al menos "
                           f"{MIN_ROTATION_VIEWS} vistas")
            missing = [n for n in names if n not in views]
            if missing:
                out.append(f"regla {index}: no existe la vista "
                           f"«{missing[0]}»")
            try:
                every = int(float(slot.get("every_minutes")))
            except (TypeError, ValueError):
                every = 0
            if not MIN_ROTATION_MINUTES <= every <= MAX_ROTATION_MINUTES:
                out.append(f"regla {index}: el intervalo va de "
                           f"{MIN_ROTATION_MINUTES} a {MAX_ROTATION_MINUTES} "
                           f"minutos")
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
        if parse_hhmm(slot.get("from")) is None:
            continue
        if parse_hhmm(slot.get("to")) is None:
            continue
        days = sorted(_days(slot.get("days")))
        if not days:
            continue
        window = {"days": days, "from": str(slot["from"]),
                  "to": str(slot["to"])}
        if kind_of(slot) == "rotation":
            # Views that no longer exist are dropped from the list rather than
            # taking the rule with them. A rotation cut to one survivor becomes
            # a fixed rule ON that view: a rule that VANISHES falls through to
            # the default, which looks exactly like a rule that did not match.
            wanted = [v for v in rotation_views(slot) if v in views]
            if not wanted:
                continue
            if len(wanted) == 1:
                slots.append({**window, "view": wanted[0]})
                continue
            slots.append({**window, "kind": "rotation", "views": wanted,
                          "every_minutes": rotation_minutes(slot)})
            continue
        if slot.get("view") not in views:
            continue
        slots.append({**window, "view": str(slot["view"])})
    out = {"default": default, "slots": slots}
    if raw.get("tz"):
        out["tz"] = str(raw["tz"])
    return out
