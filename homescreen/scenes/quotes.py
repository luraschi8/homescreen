"""Share and currency prices.

The component the surface-adaptation rule was written for. The owner put it
plainly: "It can show multiple tickers in a larger layout for the dashboard and
rotate the ticker for a small screen." Five symbols, one options set, two
presentations -- a stacked list where there is room, and one symbol at a time
where there is not.

The cycling is this component's own business. There is no platform feature for
it and there should not be: `poll_s` already lets a component ask to be woken
whenever it likes, and `poll_floor` already stops that busy-looping the
e-paper. Rotation confined to one component's code beats a field in every
component's schema.
"""

from __future__ import annotations

from homescreen import draw
from homescreen.reading import Reading
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import page

#: The Finnhub key is NOT an option here. Credentials come from the provider's
#: own SECRETS declaration, which the dashboard renders per screen and
#: globally -- one mechanism, so there is no second place a key can be set and
#: no question about which wins.
#:
#: A price and a percentage. Less demanding than the radar; about the same as
#: the clock, since it is two lines of text.
SURFACES = (# A square-ish panel, or a ticker cell in a wide band. SPEC SS9's markets
            # row is six of the latter, 127x62 each, and until this second entry
            # existed the band that row was designed for could hold nothing.
            {"min_short": 90},
            {"min_w": 110, "min_h": 40})

OPTIONS = (
    {"key": "symbols", "label": "Símbolos", "type": "text",
     "default": "AAPL",
     "help": "Separados por comas. Ej: AAPL, MSFT, BINANCE:BTCUSDT"},
    {"key": "rotate_s", "label": "Cambiar cada (s)", "type": "int",
     "default": 8,
     "help": "Sólo en pantallas pequeñas, donde cabe uno cada vez."},
)

#: How many a screen may track. Each symbol is its own fetch, and a list nobody
#: reads is still a list somebody's API quota pays for.
MAX_SYMBOLS = 8


def symbols_of(options: dict) -> tuple:
    raw = str((options or {}).get("symbols") or "")
    out, seen = [], set()
    for part in raw.split(","):
        symbol = part.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return tuple(out[:MAX_SYMBOLS])


def needs(options: dict, cfg: dict) -> tuple:
    """One fetch per symbol -- the vendor's shape, not the component's choice.

    Five screens tracking AAPL share one job, because identity is the
    parameters. The component never learns that.
    """
    return tuple({"provider": "quotes", "params": {"symbol": s}}
                 for s in symbols_of(options))


def _rotate_seconds(options: dict) -> int:
    try:
        n = int((options or {}).get("rotate_s") or 8)
    except (TypeError, ValueError):
        n = 8
    return max(2, min(300, n))


def _line(symbol: str, reading) -> str:
    change = _fmt_change(reading.get("change_pct"))[0]
    line = f"{symbol}  {_fmt_price(reading.get('price'))}"
    return f"{line}   {change}" if change else line


def _fmt_price(value) -> str:
    if value is None:
        return "--"
    value = float(value)
    # Big numbers lose their decimals rather than their leading digits: a
    # bitcoin price truncated at the front is a different number.
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _fmt_change(value) -> tuple:
    if value is None:
        return "", "dim"
    arrow = "▲" if value >= 0 else "▼"
    tone = "good" if value >= 0 else "bad"
    return f"{arrow} {abs(value):.2f}%", tone


def build(ctx: SceneContext) -> Scene:
    options = ctx.options or {}
    symbols = symbols_of(options)
    readings = {}
    for requirement in needs(options, ctx.cfg):
        symbol = requirement["params"]["symbol"]
        got = ctx.data(requirement) if callable(ctx.data) else None
        readings[symbol] = got if got is not None else Reading.nothing()

    w = int(ctx.caps.get("w") or 240)
    h = int(ctx.caps.get("h") or 240)
    stacked = len(symbols) > 1 and draw.lines_fit(
        [_line(s, readings[s]) for s in symbols], w, h,
        shape=str(ctx.caps.get("shape") or "rect"))

    if not symbols:
        return _scene(ctx, w, h, [draw.text("center", "sin símbolos", "sm", "dim")],
                      poll_s=300, body="<div class='wrap'></div>")

    if stacked:
        instructions = []
        slots = ("rim_top", "above", "center", "below", "rim_bottom")
        for symbol, slot in zip(symbols, slots):
            reading = readings[symbol]
            tone = _fmt_change(reading.get("change_pct"))[1]
            instructions.append(draw.text(slot, _line(symbol, reading), "sm",
                                          tone))
        # A list changes whenever any symbol does, so it wakes on the shortest
        # thing it shows rather than on a rotation it is not doing.
        poll_s = 60
    else:
        # One at a time. WHICH one is a function of the clock, so the device
        # does not have to remember a position and the preview can show any
        # moment -- the same reasoning as the schedule.
        every = _rotate_seconds(options)
        index = int(ctx.now // every) % len(symbols)
        symbol = symbols[index]
        reading = readings[symbol]
        change, tone = _fmt_change(reading.get("change_pct"))
        instructions = [draw.text("above", symbol, "sm", "accent"),
                        draw.text("center", _fmt_price(reading.get("price")),
                                  "lg")]
        if change:
            instructions.append(draw.text("below", change, "sm", tone))
        if len(symbols) > 1:
            instructions.append(
                draw.text("rim_bottom", f"{index + 1}/{len(symbols)}", "xs", "dim"))
        # Wake when the shown symbol changes, not on a fixed tick: the same
        # trick the clock uses for the minute boundary.
        poll_s = max(1, int(every - (ctx.now % every)))

    rows = "".join(
        f'<tr><td class="sym">{s}</td><td class="px">'
        f'{_fmt_price(readings[s].get("price"))}</td>'
        f'<td class="ch">{_fmt_change(readings[s].get("change_pct"))[0]}</td></tr>'
        for s in symbols)
    return _scene(ctx, w, h, instructions, poll_s=poll_s,
                  body=f'<div class="wrap"><table>{rows}</table></div>')


def _scene(ctx, w, h, instructions, *, poll_s, body) -> Scene:
    return Scene(layout="fill",
                 components=({"c": "quotes", "draw": instructions},),
                 poll_s=poll_s, poll_max_s=300,
                 html=page(w, h, body, CSS))


CSS = """
.wrap{padding:18px;height:100%;display:flex;align-items:center}
table{width:100%;border-collapse:collapse;font-size:22px}
td{padding:6px 0;border-bottom:1px solid #000}
.sym{font-weight:600}
.px{text-align:right;font-variant-numeric:tabular-nums}
.ch{text-align:right;width:6em;font-size:18px}
"""
