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

CSS = """
.wrap{padding:18px;display:flex;flex-direction:column;height:100%}
.big{font-size:56px;font-weight:500;letter-spacing:-.02em;line-height:1}
.sub{font-size:31px;font-weight:500;line-height:1;margin-top:14px}
.city{margin-top:4px}
.foot{margin-top:auto}
"""


def _clocks(cfg: dict, now: float) -> list[tuple[str, str]]:
    loc = cfg.get("location") or {}
    sec = cfg.get("secondary_clock") or {}
    out = []
    for label, tz in ((loc.get("name", "Madrid"), loc.get("timezone", "Europe/Madrid")),
                      (sec.get("label", "BS AS"),
                       sec.get("timezone", "America/Argentina/Buenos_Aires"))):
        try:
            stamp = datetime.fromtimestamp(now, ZoneInfo(tz))
        except Exception:  # noqa: BLE001 - a bad tz must not blank the screen
            continue
        out.append((label, stamp.strftime("%H:%M")))
    return out


def build(ctx: SceneContext) -> Scene:
    w = int(ctx.caps.get("w") or 800)
    h = int(ctx.caps.get("h") or 480)
    clocks = _clocks(ctx.cfg, ctx.now)
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
