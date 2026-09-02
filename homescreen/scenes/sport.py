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


def _when(when, now: float) -> str:
    """When a fixture is, in the shortest form that is still unambiguous.

    A weekday alone was not: "mar 18:45" on a panel you glance at could be
    this Tuesday or the one after, and a fixture list is read to plan around.
    Every form past tomorrow now carries the actual date.
    """
    local = when.astimezone()
    day = datetime.fromtimestamp(now, when.tzinfo).date()
    delta = (when.date() - day).days
    clock = local.strftime("%H:%M")
    if delta == 0:
        return f"hoy {clock}"
    if delta == 1:
        return f"mañana {clock}"
    # No leading zeros: "8/9" is what a person writes, and the column is
    # narrow enough that two saved characters are a word of team name kept.
    date = f"{local.day}/{local.month}"
    if 0 < delta < 7:
        return f"{WEEKDAYS[when.weekday()]} {date} {clock}"
    return f"{date} {clock}"


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


#: What a competition is called on the glass. The feeds answer with the legal
#: name -- "Primera Division", "UEFA Champions League" -- and a row has about
#: nine characters for it before the fixture loses its team names.
_COMPETITION_NAMES = {
    "primera division": "La Liga",
    "uefa champions league": "Champions",
    "uefa europa league": "Europa",
    "uefa europa conference league": "Conference",
    "premier league": "Premier",
    "serie a": "Serie A",
    "bundesliga": "Bundesliga",
    "ligue 1": "Ligue 1",
    "copa del rey": "Copa",
    "campeonato brasileiro série a": "Brasileirão",
    "euroleague": "Euroliga",
    "eurocup": "Eurocup",
    "f1": "F1",
}


def competition_name(raw: str) -> str:
    """A competition's name, short enough to sit beside a fixture.

    The block used to label every row with the FOLLOW's name, so a block
    following `Madrid = futbol:86` said "MADRID" against Real Madrid — Betis,
    which the row already says twice. Each fixture carries its own
    competition, and that is the thing the row does not otherwise tell you.
    """
    text = " ".join(str(raw or "").split())
    known = _COMPETITION_NAMES.get(text.casefold())
    if known:
        return known
    # An unknown competition still beats no label. "UEFA" prefixes every
    # European name and says nothing that distinguishes one from another.
    if text.casefold().startswith("uefa "):
        text = text[5:]
    return text[:14]


def is_team(provider: str, params: dict) -> bool:
    """Whether a follow names one club rather than a whole competition.

    `futbol:86` is a club and `futbol:CL` is every tie in the Champions
    League; both arrive as fixtures and only this tells them apart. It is what
    "relevance" means here -- a game your team is in outranks a game it is not.
    """
    if provider == "f1":
        return False                      # a season has no team to follow
    return bool((params or {}).get("team"))


def dedupe(entries: list) -> list:
    """One row per fixture, however many sources returned it.

    Following `Madrid = futbol:86` AND `Champions = futbol:CL` asks two
    endpoints that both answer with the same Real Madrid tie; following
    `euroliga:MAD` and `euroliga` does the same for the basketball. Measured
    against the live caches, four of thirty-nine fixtures arrived twice, and
    the block drew each of them as two rows.

    The FIRST source keeps the label, so the config's line order decides what
    a row is called -- but relevance is OR-ed across the duplicates, because
    whether your team is playing is a fact about the fixture and must not
    depend on which line the user happened to write first.
    """
    out, seen = [], {}
    for when, match in entries:
        key = (when, str(match.get("home") or "").strip().casefold(),
               str(match.get("away") or "").strip().casefold())
        if key in seen:
            kept = out[seen[key]][1]
            kept["followed"] = kept.get("followed") or match.get("followed")
            continue
        seen[key] = len(out)
        out.append((when, dict(match)))
    return out


#: How much of a block is reserved for fixtures your own teams are in. The
#: rest is kept for the competitions you follow wholesale, because a screen
#: that shows nothing but Real Madrid is not what "and other games from
#: champions and euroliga" asked for -- and over thirty days Madrid alone has
#: more fixtures than any block has rows.
_FOLLOWED_SHARE = 0.6


def rank(entries: list, limit: int) -> list:
    """The `limit` fixtures worth the space, in the order they happen.

    Relevance decides WHICH are shown; time decides the order they are shown
    IN. A list sorted by relevance reads as though the dates are shuffled,
    which is worse than useless on something you glance at.
    """
    limit = max(1, int(limit))
    mine = [e for e in entries if e[1].get("followed")]
    rest = [e for e in entries if not e[1].get("followed")]
    quota = min(len(mine), max(1, round(limit * _FOLLOWED_SHARE)))
    chosen = mine[:quota] + rest[:limit - quota]
    # One side short: the other fills the gap rather than leaving the block
    # half empty next to fixtures it could have shown.
    if len(chosen) < limit:
        taken = set(map(id, chosen))
        for entry in mine + rest:
            if len(chosen) >= limit:
                break
            if id(entry) not in taken:
                chosen.append(entry)
    return sorted(chosen, key=lambda e: e[0])


def _upcoming(matches, now: float, limit: int) -> list:
    """The fixtures a block should show, as (when, home, away, source)."""
    moment = datetime.fromtimestamp(now, timezone.utc)
    parsed = dedupe(_parse(matches))
    ahead = [(w, m) for w, m in parsed if w >= moment]
    # Nothing ahead: the most recent results, so the block says something
    # rather than collapsing between seasons.
    chosen = rank(ahead, limit) if ahead else parsed[-limit:]
    return [(_when(w, now), str(m.get("home") or ""), str(m.get("away") or ""),
             competition_name(m.get("competition")) or str(m.get("source") or ""))
            for w, m in chosen]


def build(ctx: SceneContext) -> Scene:
    options = ctx.options or {}
    wanted = needs(options, ctx.cfg)
    # Merged across every source, then sorted. Three separate blocks is
    # three lists to read; one is what you want when the Madrid game and the
    # Lakers game are on the same evening.
    followed = follows(ctx.options or {})
    readings, matches = [], []
    for requirement, (name, provider, params) in zip(wanted, followed):
        one = ctx.data(requirement) if callable(ctx.data) else None
        one = one if one is not None else Reading.nothing()
        readings.append(one)
        mine = is_team(provider, params)
        for entry in (one.get("matches") or ()):
            if isinstance(entry, dict):
                matches.append({**entry, "source": name, "followed": mine})
    reading = readings[0] if readings else Reading.nothing()
    # Shown when it DISCRIMINATES, not when there happens to be more than one
    # follow. A block following only Real Madrid still mixes La Liga with the
    # Champions League, and that is worth a label; a block that is all one
    # competition would print the same word on every row.
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
        upcoming = _upcoming(matches, ctx.now, max(1, ctx.dense_rows))
        show_source = len({src for _k, _h, _a, src in upcoming if src}) > 1
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
/* List leading, not prose leading: these are one-line rows, and 1.55em of
   line box fitted three fixtures in a block with room for five. */
.row{display:flex;gap:var(--pad-sm);align-items:baseline;
  height:var(--row-tight);font-size:var(--fs)}
.row .t{min-width:0;flex:1;font-weight:500;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.row .k{flex:none;font-size:var(--sm)}
/* Which team or series this row belongs to. Small and before the time: it
   answers a question you only ask about a row you have already read. */
.row .src{flex:none;font-size:var(--xs);letter-spacing:.06em;
  text-transform:uppercase}
""" + EMPTY_CSS
