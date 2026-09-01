"""The next match, or the last result.

Which of the two is not a setting: before kick-off you want the fixture, and
after the whistle you want the score. The component decides from the data,
because asking someone to toggle it twice a week is asking them to do the
computer's job.
"""

from __future__ import annotations

from datetime import datetime, timezone

from homescreen import draw
from homescreen.reading import Reading
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import EMPTY_CSS, empty, page

SURFACES = (
    {"variant": "strip", "at": (764, 62),
     "min_w": 200, "min_h": 24, "max_h": 110, "min_aspect": 4.0},
    # Two team names need the extra width even in a cell.
    {"variant": "badge", "at": (181, 62),
     "min_w": 150, "min_h": 40, "max_h": 110, "max_aspect": 4.0},
    # v6's DEPORTES block: three fixtures.
    {"variant": "card", "at": (417, 150),
     "min_short": 90, "min_h": 111, "max_h": 239},
    {"variant": "panel", "at": (417, 335),
     "min_short": 90, "min_w": 200, "min_h": 240},
)

OPTIONS = (
    {"key": "team", "label": "ID del equipo", "type": "int", "default": 0,
     "help": "Ver football-data.org. 86 = Real Madrid, 81 = Barcelona."},
    {"key": "days", "label": "Días por delante", "type": "int", "default": 30},
)

#: A score changes during a match and nothing else does. Five minutes is the
#: compromise: quick enough that a result is not stale by the time you look,
#: slow enough that a screen is not a scoreboard.
POLL_S = 300

FINISHED = {"FINISHED", "AWARDED"}
LIVE = {"IN_PLAY", "PAUSED"}
WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def needs(options: dict, cfg: dict) -> tuple:
    try:
        team = int((options or {}).get("team") or 0)
    except (TypeError, ValueError):
        return ()
    if team <= 0:
        return ()
    try:
        days = int((options or {}).get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    return ({"provider": "football", "params": {"team": team, "days": days}},)


def _pick(matches, now: float):
    """The match worth showing: one in play, else the next, else the last.

    In that order because a live match beats everything, a fixture beats a
    result you have already seen, and an old result beats an empty screen.
    """
    parsed = _parse(matches)
    if not parsed:
        return None, None
    for when, match in parsed:
        if match.get("status") in LIVE:
            return when, match
    moment = datetime.fromtimestamp(now, timezone.utc)
    for when, match in parsed:
        if when >= moment and match.get("status") not in FINISHED:
            return when, match
    return parsed[-1]


def _when(when, now: float) -> str:
    day = datetime.fromtimestamp(now, when.tzinfo).date()
    delta = (when.date() - day).days
    clock = when.astimezone().strftime("%H:%M")
    if delta == 0:
        return f"hoy {clock}"
    if delta == 1:
        return f"mañana {clock}"
    if 0 < delta < 7:
        return f"{WEEKDAYS[when.weekday()]} {clock}"
    return when.astimezone().strftime("%d/%m %H:%M")


def _parse(matches) -> list:
    """(datetime, match) for every readable fixture, in time order.

    Shared with `_pick`, so a block and a cell agree on what the fixtures ARE
    and differ only in how many they show.
    """
    parsed = []
    for match in matches or ():
        if not isinstance(match, dict):
            continue
        try:
            when = datetime.fromisoformat(
                str(match.get("when", "")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        parsed.append((when, match))
    parsed.sort(key=lambda p: p[0])
    return parsed


def _upcoming(matches, now: float, limit: int) -> list:
    """The next `limit` fixtures as (when, home, away).

    `_pick` answers with ONE, which is right for a cell and wrong for a block:
    the design's DEPORTES section lists three.
    """
    moment = datetime.fromtimestamp(now, timezone.utc)
    ahead = [(w, m) for w, m in _parse(matches) if w >= moment]
    # Nothing ahead: the most recent results, so the block says something
    # rather than collapsing between seasons.
    chosen = ahead[:limit] or _parse(matches)[-limit:]
    return [(_when(w, now), str(m.get("home") or ""), str(m.get("away") or ""))
            for w, m in chosen]


def build(ctx: SceneContext) -> Scene:
    options = ctx.options or {}
    wanted = needs(options, ctx.cfg)
    reading = (ctx.data(wanted[0]) if wanted and callable(ctx.data) else None)
    reading = reading if reading is not None else Reading.nothing()
    when, match = _pick(reading.get("matches"), ctx.now)

    w = int(ctx.caps.get("w") or 240)
    h = int(ctx.caps.get("h") or 240)

    if not wanted:
        instructions = [draw.text("center", "sin equipo", "sm", "dim"),
                        draw.text("below", "elige uno en los ajustes", "xs", "dim")]
    elif match is None:
        instructions = [draw.text("center", "sin partidos", "md"),
                        draw.text("below", "en el periodo elegido", "xs", "dim")]
    else:
        home, away = match.get("home", ""), match.get("away", "")
        finished = match.get("status") in FINISHED
        live = match.get("status") in LIVE
        if finished or live:
            score = f"{match.get('home_goals', 0)} - {match.get('away_goals', 0)}"
            head = "en juego" if live else _when(when, ctx.now)
            instructions = [draw.text("above", head, "xs",
                                      "good" if live else "dim"),
                            draw.text("center", score, "lg")]
        else:
            instructions = [draw.text("above", _when(when, ctx.now), "sm", "dim"),
                            draw.text("center", "vs", "sm", "dim")]
        # One instruction per slot, always. Both branches used to append a
        # second string to a slot another line already held, and neither the
        # resolver nor the firmware deduplicates -- so the panel printed
        # "AtleticoladeligaMadrid", two words overprinted into one.
        shape = str(ctx.caps.get("shape") or "rect")
        pairing = f"{home}  {away}" if not (finished or live) else \
            f"{home}  ·  {away}"
        if not draw.lines_fit([pairing], w, h, shape=shape):
            pairing = f"{home[:11]}  {away[:11]}"
        instructions.insert(0, draw.text("rim_top",
                                         str(match.get("competition") or "")
                                         or pairing, "xs", "dim"))
        instructions.append(draw.text(
            "below" if (finished or live) else "rim_bottom", pairing, "xs",
            "accent"))

    # A bare em dash is what this rendered with no team configured, which
    # says nothing at all -- while the same component on the round panel said
    # "elige uno en los ajustes".
    if not wanted:
        inner = empty("sin equipo", "elige uno en los ajustes")
    elif match is None:
        inner = empty("sin partidos", "en el periodo elegido")
    elif ctx.variant in ("card", "panel"):
        # A fixture with no date is not information. The draw list has
        # computed the kickoff all along and the HTML threw it away.
        upcoming = _upcoming(reading.get("matches"), ctx.now,
                             max(1, ctx.rows))
        inner = '<div class="list">' + "".join(
            f'<div class="row"><div class="t">{home} — {away}</div>'
            f'<div class="k">{kick}</div></div>'
            for kick, home, away in upcoming) + "</div>"
    else:
        inner = (f'<div class="big">{match.get("home", "")} — '
                 f'{match.get("away", "")}</div>')
    body = f'<div class="wrap">{inner}</div>' 
    return Scene(layout="fill",
                 components=({"c": "sport", "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S,
                 html=page(w, h, body, CSS, shape=ctx.variant))


CSS = """
.wrap{padding:var(--pad);height:100%;display:flex;align-items:center;
  justify-content:center}
.big{font-size:var(--sub);font-weight:600;text-align:center}
.list{display:flex;flex-direction:column;justify-content:center;
  width:100%;height:100%}
.row{display:flex;gap:var(--pad-sm);align-items:baseline;
  padding:var(--pad-sm) 0;font-size:var(--fs)}
.row .t{min-width:0;flex:1;font-weight:500;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.row .k{flex:none;font-size:var(--sm)}
""" + EMPTY_CSS
