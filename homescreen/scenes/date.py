"""The masthead: what day it is, and how fresh the panel is.

The original design's masthead is a sun, "Lunes 24 de agosto", and
"actualizado 14:32" right-aligned. Nothing could produce that line: the only
component that fitted an 800x53 band was the clock, which drew a time the
left column was already showing and used a tenth of the width.

The freshness stamp is CLAUDE.md's rule, not decoration -- it must reflect the
OLDEST successful fetch, so a fetcher that quietly died is visible on the
glass instead of the panel showing confident stale numbers.
"""

from __future__ import annotations

import datetime
import zoneinfo

from homescreen import draw
from homescreen.config import home_location
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._icons import sky as sky_icon
from homescreen.scenes._style import page

#: A date reads at any size; only how much of it changes.
SURFACES = (
    {"variant": "strip", "at": (800, 53),
     "min_w": 200, "max_h": 80, "min_aspect": 6.0},
    {"variant": "badge", "at": (127, 62),
     "min_w": 90, "max_w": 199, "max_h": 80},
    {"variant": "card", "at": (417, 150),
     "min_short": 40, "min_h": 81, "max_h": 239},
    {"variant": "panel", "at": (321, 335), "min_h": 240},
)

OPTIONS = (
    {"key": "timezone", "label": "Zona horaria", "type": "text", "default": "",
     "help": "En blanco usa la ubicación del servidor."},
    {"key": "show_updated", "label": "Mostrar «actualizado»", "type": "bool",
     "default": True},
)

#: Spelled out rather than taken from the locale. `strftime("%A")` follows
#: LC_TIME, the Pi has no Spanish locale generated, and everything that lands
#: on glass is Spanish -- the weekday came out "Monday".
_DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado",
         "domingo")
_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre")

#: Once a minute is pointless for a date, and once a day would leave a panel
#: showing yesterday until lunchtime. The stamp moves, so this follows it.
POLL_S = 600

#: How much of the band the date may take, by shape.
#:
#: It rendered at 10px -- the floor CLAUDE.md sets for the SMALLEST legible
#: thing on the panel -- in a band 53 pixels tall, because the shared body
#: size is `inner * 0.22` and that factor assumes a region stacking four or
#: five rows. A masthead holds one line, so it can afford far more of its
#: height, and only the component knows that.
_HERO_SHARE = {"strip": 0.38, "badge": 0.40, "card": 0.26, "panel": 0.18}


def _zone(ctx: SceneContext):
    name = str((ctx.options or {}).get("timezone") or "").strip()
    if not name:
        # `timezone`, not `tz`: that is the key `location:` actually carries
        # and the one `clock.py` reads. `.get("tz")` was always None, so the
        # masthead silently fell back to the SERVER PROCESS's local time --
        # invisible only because this Pi happens to run in Europe/Madrid. Under
        # TZ=UTC it puts the wrong day on the glass for two hours every night.
        loc = home_location(ctx.cfg or {})
        name = str(loc.get("timezone") or loc.get("tz") or "").strip()
    if name:
        try:
            return zoneinfo.ZoneInfo(name)
        except Exception:                               # noqa: BLE001
            pass                                        # fall through to local
    return None


def _long(moment: datetime.datetime) -> str:
    return (f"{_DAYS[moment.weekday()]} {moment.day} de "
            f"{_MONTHS[moment.month - 1]}")


def _short(moment: datetime.datetime) -> str:
    return f"{_DAYS[moment.weekday()][:3]} {moment.day}"


def build(ctx: SceneContext) -> Scene:
    w = int(ctx.caps.get("w") or 800)
    h = int(ctx.caps.get("h") or 53)
    moment = datetime.datetime.fromtimestamp(ctx.now, _zone(ctx))

    long_form, short_form = _long(moment), _short(moment)
    # The OLDEST successful fetch, not the clock. A panel whose weather died
    # an hour ago must not print the current time next to the word
    # "actualizado" -- that is the confident-stale-data failure CLAUDE.md
    # names, wearing a badge that says it is fine.
    stale = getattr(ctx, "oldest_fetch", None)
    stamp = ""
    if (ctx.options or {}).get("show_updated", True):
        when = datetime.datetime.fromtimestamp(stale or ctx.now, _zone(ctx))
        stamp = f"actualizado {when:%H:%M}"

    icon = sky_icon("clear", 20) if ctx.variant == "strip" else ""
    if ctx.variant in ("strip", "badge"):
        text = long_form if ctx.variant == "strip" else short_form
        tail = f'<div class="upd">{stamp}</div>' if stamp and \
            ctx.variant == "strip" else ""
        body = (f'<div class="wrap row">{icon}'
                f'<div class="d">{text}</div>{tail}</div>')
    else:
        body = (f'<div class="wrap"><div class="d">{long_form}</div>'
                f'<div class="upd">{stamp}</div></div>')

    instructions = [draw.text("center", short_form, "md"),
                    draw.text("below", stamp, "xs", "dim")] if stamp else \
        [draw.text("center", short_form, "md")]

    return Scene(layout="fill",
                 components=({"c": "date", "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S,
                 html=page(w, h, body, CSS, shape=ctx.variant,
                           hero_share=_HERO_SHARE.get(ctx.variant)))


CSS = """
.wrap{padding:var(--pad);height:100%;display:flex;flex-direction:column;
  justify-content:center}
/* Relative to the date, not to the band's padding: `--pad-sm` is 1px in a
   band this shallow, which put the glyph hard against the "m". */
.wrap.row{flex-direction:row;align-items:center;gap:.45em;
  font-size:var(--hero)}
.ic{flex:none;display:block}
.d{font-size:var(--hero);font-weight:500;letter-spacing:.02em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.1}
/* Pushed to the far end, as the design has it: the date is the headline and
   the freshness is a footnote you only read when something looks wrong. */
.upd{margin-left:auto;font-size:var(--xs);letter-spacing:.06em;flex:none}
"""
