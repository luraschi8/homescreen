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
    # A genuine band: shallow AND long. The real ones are 800x53 (aspect 15)
    # and 764x62 (12.3); at aspect 4.0 and 110px tall this was swallowing
    # blocks with room for a list.
    {"variant": "strip", "at": (764, 62),
     "min_w": 200, "min_h": 24, "max_h": 80, "min_aspect": 6.0},
    # A cell of a band: narrow AND shallow. Bounded on both, because bounding
    # only the width left it overlapping `card` in a small square -- and an
    # overlap is the ordering hazard back again.
    {"variant": "badge", "at": (181, 62),
     "min_w": 150, "max_w": 199, "min_h": 40, "max_h": 80},
    # A block: several rows. v6's AGENDA is 417x104 and DEPORTES 417x50
    # after their headings.
    {"variant": "card", "at": (417, 150),
     "min_short": 90, "min_h": 81, "max_h": 239},
    {"variant": "panel", "at": (417, 335),
     "min_short": 90, "min_h": 240},
)

#: Which sources a line may name. The identifier after the colon is a TEAM or
#: a COMPETITION -- "every Champions League tie" is what you want from a
#: tournament you follow but have no club in, and "Madrid" is what you want
#: from a league you follow one club in.
SOURCES = {"futbol": "football", "nba": "nba", "f1": "f1",
           "euroliga": "euroleague", "eurocup": "euroleague"}

OPTIONS = (
    {"key": "teams", "label": "Equipos", "type": "lines", "default": "",
     "help": "Uno por línea. Equipo: «Madrid = futbol:86», "
             "«Lakers = nba:LAL», «Madrid = euroliga:MAD». Competición "
             "entera: «Champions = futbol:CL», «Euroliga = euroliga», "
             "«F1 = f1». El nombre es opcional y aparece en cada fila cuando "
             "hay más de uno."},
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


def follows(options: dict) -> list:
    """(name, provider, params) for every team or series this block shows.

    One per line, `Nombre = fuente:id`. `futbol` wants a numeric
    football-data.org team, `nba` a three-letter code, `f1` nothing at all --
    a season has no team to follow.

    `team` is still read, because records written before this exist and must
    keep working without a migration.
    """
    try:
        days = int((options or {}).get("days") or 30)
    except (TypeError, ValueError):
        days = 30

    lines = [ln.strip() for ln in
             str((options or {}).get("teams") or "").replace("\r", "").split("\n")]
    legacy = str((options or {}).get("team") or "").strip()
    if legacy and legacy not in ("0", ""):
        lines.append(f"futbol:{legacy}")

    out, seen = [], set()
    for line in lines:
        if not line:
            continue
        name, sep, rest = line.partition("=")
        if not sep:
            name, rest = "", line
        name, rest = name.strip()[:16], rest.strip()
        source, _, ident = rest.partition(":")
        provider = SOURCES.get(source.strip().lower())
        if not provider:
            continue                     # a source we cannot fetch is dropped
        ident = ident.strip()
        if provider == "football":
            # A number is a club, letters are a competition. Nothing else
            # distinguishes them, and football-data.org uses different
            # endpoints for each.
            if ident.isdigit():
                params = {"team": int(ident), "days": days}
            elif ident.isalnum() and 2 <= len(ident) <= 5:
                params = {"competition": ident.upper(), "days": days}
            else:
                continue
        elif provider == "nba":
            params = {"team": ident.upper(), "days": days}
        elif provider == "euroleague":
            params = {"competition": "U" if source.strip().lower() == "eurocup"
                      else "E", "team": ident.upper(), "days": days}
        else:
            params = {"season": "current"}
        key = (provider, tuple(sorted(params.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append((name, provider, params))
    return out


def needs(options: dict, cfg: dict) -> tuple:
    return tuple({"provider": provider, "params": params}
                 for _, provider, params in follows(options))


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
    return [(_when(w, now), str(m.get("home") or ""), str(m.get("away") or ""),
             str(m.get("source") or "")) for w, m in chosen]


def build(ctx: SceneContext) -> Scene:
    options = ctx.options or {}
    wanted = needs(options, ctx.cfg)
    # Merged across every source, then sorted. Three separate blocks is
    # three lists to read; one is what you want when the Madrid game and the
    # Lakers game are on the same evening.
    followed = follows(ctx.options or {})
    readings, matches = [], []
    for requirement, (name, _p, _q) in zip(wanted, followed):
        one = ctx.data(requirement) if callable(ctx.data) else None
        one = one if one is not None else Reading.nothing()
        readings.append(one)
        for entry in (one.get("matches") or ()):
            if isinstance(entry, dict):
                matches.append({**entry, "source": name})
    reading = readings[0] if readings else Reading.nothing()
    # A marker on every row of a single-team block says the same thing three
    # times.
    show_source = len([n for n, _p, _q in followed if n]) > 1
    when, match = _pick(matches, ctx.now)

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
        inner = empty("sin equipo", "elige uno en los ajustes", ctx.variant)
    elif match is None:
        inner = empty("sin partidos", "en el periodo elegido", ctx.variant)
    elif ctx.variant in ("card", "panel"):
        # A fixture with no date is not information. The draw list has
        # computed the kickoff all along and the HTML threw it away.
        upcoming = _upcoming(matches, ctx.now, max(1, ctx.rows))
        inner = '<div class="list">' + "".join(
            f'<div class="row"><div class="t">{home} — {away}</div>'
            + (f'<div class="src">{src}</div>' if show_source and src else "")
            + f'<div class="k">{kick}</div></div>'
            for kick, home, away, src in upcoming) + "</div>"
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
/* Which team or series this row belongs to. Small and before the time: it
   answers a question you only ask about a row you have already read. */
.row .src{flex:none;font-size:var(--xs);letter-spacing:.06em;
  text-transform:uppercase}
""" + EMPTY_CSS
