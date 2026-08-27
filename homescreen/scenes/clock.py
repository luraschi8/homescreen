"""Clock: the two cities.

Renders both ways from one source of truth. The e-paper gets HTML the Pi
rasterises; a self-drawing panel gets a `clock` component carrying an
instruction list -- "this text, this slot, this size" -- which the firmware
executes directly and `homescreen.draw` executes onto a PNG for the dashboard
preview. Neither invents layout, so the preview cannot drift from the glass.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from homescreen.scenes import Scene, SceneContext
from homescreen import draw
from homescreen.scenes._style import page

#: What an operator can set per assignment. The dashboard renders fields from
#: this, the registry stores the values against the assignment, and
#: scenes.clean_options coerces them -- so adding an option is one edit here
#: rather than three that can disagree.
OPTIONS = (
    {"key": "timezone", "label": "Timezone", "type": "text",
     "default": "", "help": "IANA name, e.g. Europe/Madrid. Blank uses the "
                            "server's own location."},
    {"key": "second_label", "label": "Second city", "type": "text",
     "default": "", "help": "Blank hides the second line."},
    {"key": "second_timezone", "label": "Second timezone", "type": "text",
     "default": ""},
    {"key": "show_seconds", "label": "Show seconds", "type": "bool",
     "default": False},
)

CSS = """
.wrap{padding:18px;display:flex;flex-direction:column;height:100%}
.big{font-size:56px;font-weight:500;letter-spacing:-.02em;line-height:1}
.sub{font-size:31px;font-weight:500;line-height:1;margin-top:14px}
.city{margin-top:4px}
.foot{margin-top:auto}
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


def build(ctx: SceneContext) -> Scene:
    w = int(ctx.caps.get("w") or 800)
    h = int(ctx.caps.get("h") or 480)
    clocks = _clocks(ctx.cfg, ctx.now, ctx.options or {})
    if not clocks:
        clocks = [("", "--:--")]
    primary, rest = clocks[0], clocks[1:]
    body = [f'<div class="wrap"><div class="big">{primary[1]}</div>',
            f'<div class="lab city">{primary[0]}</div>']
    for label, value in rest:
        body.append(f'<div class="sub">{value}</div>'
                    f'<div class="lab city">{label}</div>')
    stamp = datetime.fromtimestamp(ctx.now).strftime("%Y-%m-%d %H:%M")
    body.append(f'<div class="foot"><div class="rule"></div>'
                f'<div class="ter" style="margin-top:6px">{stamp}</div></div></div>')

    # The same clocks as instructions. A 240x240 round panel has room for one
    # time and its label plus a second city small at the rim -- deciding that
    # here, once, is what lets the preview be exact.
    instructions = [draw.text("center", primary[1], "xl"),
                    draw.text("below", primary[0], "sm", "dim")]
    if rest:
        instructions.append(
            draw.text("rim_bottom", f"{rest[0][0]} {rest[0][1]}", "xs", "dim"))
    components = ({"c": "clock", "draw": instructions},)

    return Scene(layout="fill", components=components,
                 html=page(w, h, "".join(body), CSS))
