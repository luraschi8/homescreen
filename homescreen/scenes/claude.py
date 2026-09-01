"""How much Claude this organisation has used.

Deliberately one number and a period. A usage figure on a wall is a glance, not
a report -- if you need the breakdown you are already at a computer.
"""

from __future__ import annotations

from homescreen import draw
from homescreen.reading import Reading
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import page

SURFACES = ({"min_short": 90},
            {"min_w": 110, "min_h": 40})   # a number and its label

OPTIONS = (
    {"key": "days", "label": "Días", "type": "int", "default": 30,
     "help": "Periodo que se suma."},
)

POLL_S = 900


def needs(options: dict, cfg: dict) -> tuple:
    try:
        days = int((options or {}).get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    return ({"provider": "claude_usage", "params": {"days": days}},)


def _short(value) -> str:
    """1.2M rather than 1234567: a wall wants the magnitude."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "--"
    for limit, suffix in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= limit:
            return f"{n / limit:.1f}{suffix}"
    return f"{int(n)}"


def build(ctx: SceneContext) -> Scene:
    options = ctx.options or {}
    wanted = needs(options, ctx.cfg)
    reading = ctx.data(wanted[0]) if callable(ctx.data) else None
    reading = reading if reading is not None else Reading.nothing()

    w = int(ctx.caps.get("w") or 240)
    h = int(ctx.caps.get("h") or 240)
    days = reading.get("days") or options.get("days") or 30

    if reading.missing:
        instructions = [draw.text("center", "--", "xl"),
                        draw.text("below", "sin datos de uso", "xs", "dim")]
    else:
        instructions = [
            draw.text("center", _short(reading.get("total_tokens")), "xl",
                      "accent"),
            draw.text("below", f"tokens · {days} días", "sm", "dim"),
            draw.text("rim_bottom",
                      f"in {_short(reading.get('input_tokens'))} · "
                      f"out {_short(reading.get('output_tokens'))}", "xs", "dim"),
        ]
    body = (f'<div class="wrap"><div class="big">'
            f'{_short(reading.get("total_tokens"))}</div>'
            f'<div class="lab">tokens · {days} días</div></div>')
    return Scene(layout="fill",
                 components=({"c": "claude", "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S, html=page(w, h, body, CSS))


CSS = """
.wrap{padding:var(--pad);height:100%;display:flex;flex-direction:column;
  justify-content:center}
.big{font-size:var(--hero);font-weight:600;line-height:1}
.lab{font-size:var(--lg);margin-top:var(--pad-sm)}
"""
