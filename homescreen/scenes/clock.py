"""Clock: the two cities.

Renders both ways from one source of truth. The e-paper gets HTML the Pi
rasterises; a self-drawing panel gets a `clock` component carrying an
instruction list -- "this text, this slot, this size" -- which the firmware
executes directly and `homescreen.draw` executes onto a PNG for the dashboard
preview. Neither invents layout, so the preview cannot drift from the glass.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from homescreen.scenes import Scene, SceneContext
from homescreen import draw
from homescreen.scenes._icons import sun_event as sun_icon
from homescreen.scenes._style import page

#: What an operator can set per assignment. The dashboard renders fields from
#: this, the registry stores the values against the assignment, and
#: scenes.clean_options coerces them -- so adding an option is one edit here
#: rather than three that can disagree.
#: A clock is the least demanding thing here: a time and a label fit
#: anywhere text is legible at all, on any shape, at any depth.
#: The clock reads at any size -- it is a number.
SURFACES = (
    # A genuine band: shallow AND long. The real ones are 800x53 (aspect 15)
    # and 764x62 (12.3); at aspect 4.0 and 110px tall this was swallowing
    # blocks with room for a list.
    {"variant": "strip", "at": (764, 62),
     "min_w": 200, "min_h": 24, "max_h": 80, "min_aspect": 6.0},
    # A cell of a band: narrow AND shallow. Bounded on both, because bounding
    # only the width left it overlapping `card` in a small square -- and an
    # overlap is the ordering hazard back again.
    {"variant": "badge", "at": (127, 62),
     "min_w": 90, "max_w": 199, "min_h": 40, "max_h": 80},
    # A block: several rows. v6's AGENDA is 417x104 and DEPORTES 417x50
    # after their headings.
    {"variant": "card", "at": (417, 150),
     "min_short": 90, "min_h": 81, "max_h": 239},
    {"variant": "panel", "at": (417, 335),
     "min_short": 90, "min_h": 240},
)

OPTIONS = (
    {"key": "show_sun", "label": "Mostrar amanecer y atardecer", "type": "bool",
     "default": True,
     "help": "Usa el tiempo que ya trae esta pantalla; no pide nada extra."},
    {"key": "timezone", "label": "Zona horaria", "type": "text",
     "default": "",
     # The dashboard renders this as a list the browser filters as you type.
     # It was a free-text box, which demanded you already knew that Madrid is
     # spelled "Europe/Madrid".
     "datalist": "timezones",
     "help": "En blanco usa la ubicación del servidor."},
    {"key": "second_label", "label": "Segunda ciudad", "type": "text",
     "default": "", "help": "En blanco oculta la segunda línea."},
    {"key": "second_timezone", "label": "Zona de la segunda ciudad",
     "type": "text", "default": "", "datalist": "timezones"},
    {"key": "show_seconds", "label": "Mostrar segundos", "type": "bool",
     "default": False},
)

CSS = """
.wrap{padding:var(--pad);display:flex;flex-direction:column;height:100%}
.big{font-size:var(--hero);font-weight:500;letter-spacing:-.02em;line-height:1}
.sub{font-size:var(--sub);font-weight:500;line-height:1;
  margin-top:var(--pad-sm)}
.city{margin-top:4px}
/* A band: everything along the line, with air between the pairs. */
.wrap.row{flex-direction:row;align-items:baseline;gap:.35em}
.wrap.row .big{line-height:1}
.wrap.row .sub{margin-top:0;margin-left:.9em}
.wrap.row .lab{align-self:center}

/* Side by side: time over city, sun times, a rule, the second city. */
.wrap.block{flex-direction:row;align-items:flex-end;gap:.7em;
  justify-content:space-between}
/* Each group takes its share of the width so the block fills the region
   instead of collapsing to the left and leaving half of it blank. */
.wrap.block > *{flex:0 1 auto}
.wrap.block .c{flex:1 1 auto}
.wrap.block .c{min-width:0}
.wrap.block .big{line-height:1}
.wrap.block .sub{margin-top:0}
/* Where the mockup drew an obelisk. A 1px rule for a fiftieth of the ink. */
.wrap.block .bar{width:1px;align-self:stretch;background:#000;
  margin:0 .2em}
.wrap.block .sun{display:flex;flex-direction:column;gap:2px;
  font-size:var(--sm);font-weight:500}
.wrap.block .sun div{display:flex;align-items:center;gap:3px;
  white-space:nowrap}
"""


def _clocks(cfg: dict, now: float, options: dict) -> list[tuple[str, str]]:
    """The cities to show. The assignment's options win over config.yaml, so two
    screens can show different places without the server holding two configs."""
    loc = cfg.get("location") or {}
    sec = cfg.get("secondary_clock") or {}
    fmt = "%H:%M:%S" if options.get("show_seconds") else "%H:%M"

    primary_tz = options.get("timezone") or loc.get("timezone", "Europe/Madrid")
    primary_label = loc.get("name", "Madrid")
    if options.get("timezone"):
        # A configured zone names itself: "Europe/Madrid" -> "Madrid".
        primary_label = str(options["timezone"]).rsplit("/", 1)[-1].replace("_", " ")

    pairs = [(primary_label, primary_tz)]
    second_tz = options.get("second_timezone") or sec.get("timezone")
    second_label = options.get("second_label") or sec.get("label")
    # An explicitly blank second_label hides the line; an unset one falls back.
    if second_tz and (second_label or "second_label" not in options):
        pairs.append((second_label or "", second_tz))

    out = []
    for label, tz in pairs:
        try:
            stamp = datetime.fromtimestamp(now, ZoneInfo(tz))
        except Exception:  # noqa: BLE001 - a bad tz must not blank the screen
            continue
        out.append((label, stamp.strftime(fmt)))
    return out


#: How much of the block the time may take, by shape. Generous, because a
#: clock has nothing under it but its own city name.
_HERO_SHARE = {"card": 0.62, "panel": 0.42, "badge": 0.44}


def _sun(ctx) -> str:
    """Sunrise and sunset, beside the clock, as the v6 design has them.

    Read from whatever weather reading this screen already has rather than
    fetched again: the envelope has carried `sunrise`/`sunset` since the day
    the fields were added and nothing has read them until now.
    """
    if not (ctx.options or {}).get("show_sun", True):
        return ""
    try:
        from homescreen.scenes import weather as _weather
        wanted = _weather.needs(ctx.options or {}, ctx.cfg)
        reading = ctx.data(wanted[0]) if wanted and callable(ctx.data) else None
    except Exception:                                   # noqa: BLE001
        return ""
    if reading is None:
        return ""
    rise, set_ = reading.get("sunrise"), reading.get("sunset")
    if rise is None or set_ is None:
        return ""
    offset = reading.get("tz_offset_s") or 0

    def clock(stamp):
        moment = datetime.fromtimestamp(int(stamp) + int(offset), timezone.utc)
        return moment.strftime("%H:%M")

    return (f'<div>{sun_icon("sunrise", 13)}<span>{clock(rise)}</span></div>'
            f'<div>{sun_icon("sunset", 13)}<span>{clock(set_)}</span></div>')


def _body(variant: str, primary, rest, sun: str = "") -> str:
    """The arrangement for this SHAPE.

    It used to stack vertically at every size, and then append a rule and an
    ISO timestamp. In an 800x53 masthead that is a hero, two labels, a second
    time and a rule inside 53 pixels: everything below the first line was
    clipped, and the rule was the stray divider showing under the masthead.

    The stamp is gone entirely. `2026-09-01 14:07` is an operator artefact on
    Spanish-facing glass, and the masthead's own `actualizado` is the honest
    version of what it was trying to say.
    """
    if variant in ("strip", "badge"):
        # Along the line, because a band has no room to stack a label under a
        # number -- which is why the second city was invisible in the header.
        parts = [f'<span class="big">{primary[1]}</span>',
                 f'<span class="lab">{primary[0]}</span>']
        for label, value in rest:
            parts.append(f'<span class="sub">{value}</span>'
                         f'<span class="lab">{label}</span>')
        return f'<div class="wrap row">{"".join(parts)}</div>'

    # The design's clock block, side by side. Each city is its time with its
    # name UNDER it, the sun times sit beside the first, and a 1px rule stands
    # where the mockup drew a stone obelisk -- which is a grey illustration
    # and thresholds to nothing, so it becomes the divider it was acting as.
    columns = [f'<div class="c"><div class="big">{primary[1]}</div>'
               f'<div class="lab">{primary[0]}</div></div>']
    if sun:
        columns.append(f'<div class="sun">{sun}</div>')
    for label, value in rest:
        columns.append('<div class="bar"></div>')
        columns.append(f'<div class="c"><div class="sub">{value}</div>'
                       f'<div class="lab">{label}</div></div>')
    return f'<div class="wrap block">{"".join(columns)}</div>'



def build(ctx: SceneContext) -> Scene:
    w = int(ctx.caps.get("w") or 800)
    h = int(ctx.caps.get("h") or 480)
    clocks = _clocks(ctx.cfg, ctx.now, ctx.options or {})
    if not clocks:
        clocks = [("", "--:--")]
    primary, rest = clocks[0], clocks[1:]
    body = _body(ctx.variant, primary, rest, _sun(ctx))

    # The same clocks as instructions. A 240x240 round panel has room for one
    # time and its label plus a second city small at the rim -- deciding that
    # here, once, is what lets the preview be exact.
    instructions = [draw.text("center", primary[1], "xl"),
                    draw.text("below", primary[0], "sm", "accent")]
    if rest:
        instructions.append(
            draw.text("rim_bottom", f"{rest[0][0]} {rest[0][1]}", "xs", "dim"))
    components = ({"c": "clock", "draw": instructions},)

    # Ask to be woken when the picture actually changes. A clock is stale the
    # instant the minute rolls and identical for the 59 seconds after it, so a
    # fixed grid is the worst of both: twelve requests a minute, and the new
    # minute still arriving up to a poll late. With seconds shown there is no
    # boundary to aim at and every second is a change.
    opts = ctx.options or {}
    seconds = bool(opts.get("show_seconds"))
    poll_s = 1 if seconds else max(1, 60 - int(ctx.now) % 60)

    return Scene(layout="fill", components=components,
                 poll_s=poll_s, poll_max_s=1 if seconds else 60,
                 # A clock is one number with a small label under it, so it may take
                 # most of its block -- unlike a weather panel, which stacks
                 # three things beneath its headline.
                 html=page(w, h, body, CSS, shape=ctx.variant,
                           hero_share=_HERO_SHARE.get(ctx.variant)))
