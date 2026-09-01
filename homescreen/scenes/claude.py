"""How much Claude this organisation has used.

Deliberately one number and a period. A usage figure on a wall is a glance, not
a report -- if you need the breakdown you are already at a computer.
"""

from __future__ import annotations

from homescreen import draw
from homescreen.reading import Reading
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import page

SURFACES = (
    {"variant": "strip", "at": (764, 62),
     "min_w": 200, "min_h": 24, "max_h": 110, "min_aspect": 4.0},
    {"variant": "badge", "at": (127, 62),
     "min_w": 90, "min_h": 40, "max_h": 110, "max_aspect": 4.0},
    {"variant": "card", "at": (417, 150),
     "min_short": 90, "min_h": 111, "max_h": 239},
    {"variant": "panel", "at": (417, 335),
     "min_short": 90, "min_w": 200, "min_h": 240},
)

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
            # A cell is 116px wide. "tokens · 30 días" wrapped to two lines
            # and pushed itself out of a 62px band; the unit is what matters
            # there and the period is a detail for a block.
            f'<div class="cu-label">'
            f'{"tokens" if ctx.variant == "badge" else f"tokens · {days} días"}'
            f'</div></div>')
    return Scene(layout="fill",
                 components=({"c": "claude", "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S,
                 html=page(w, h, body, CSS, shape=ctx.variant))


CSS = """
.wrap{padding:var(--pad);height:100%;display:flex;flex-direction:column;
  justify-content:center}
.big{font-size:var(--hero);font-weight:600;line-height:1}
.cu-label{font-size:var(--xs);margin-top:2px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
"""
