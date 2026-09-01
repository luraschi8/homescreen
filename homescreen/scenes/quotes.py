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
from homescreen.scenes import _icons
from homescreen.scenes._style import EMPTY_CSS, empty, page

#: The Finnhub key is NOT an option here. Credentials come from the provider's
#: own SECRETS declaration, which the dashboard renders per screen and
#: globally -- one mechanism, so there is no second place a key can be set and
#: no question about which wins.
#:
#: A price and a percentage. Less demanding than the radar; about the same as
#: the clock, since it is two lines of text.
#: DISJOINT, so the order these are written in does not matter. See
#: `weather.SURFACES` for why maximums are what make that true.
SURFACES = (
    # The whole band: every symbol along it, evenly spread.
    {"variant": "strip", "at": (764, 62),
     "min_w": 200, "min_h": 24, "max_h": 110, "min_aspect": 4.0},
    # One cell of a markets band. SPEC SS9's row is six of these, and the
    # design stacks three lines in each: symbol, price, change.
    {"variant": "badge", "at": (127, 62),
     "min_w": 90, "min_h": 40, "max_h": 110, "max_aspect": 4.0},
    # A block: as many rows as fit.
    {"variant": "card", "at": (417, 150),
     "min_short": 90, "min_h": 111, "max_h": 239},
    # A column of them.
    {"variant": "panel", "at": (321, 335),
     "min_short": 90, "min_w": 200, "min_h": 240},
)

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
        # The same thing the draw list says. An empty `.wrap` is a silent
        # band on the panel, and silence reads as a broken renderer.
        return _scene(ctx, w, h,
                      [draw.text("center", "sin símbolos", "sm", "dim")],
                      poll_s=300,
                      body=f'<div class="wrap">'
                           f'{empty("sin símbolos", "añade alguno en los ajustes", ctx.variant)}'
                           f'</div>')

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

    return _scene(ctx, w, h, instructions, poll_s=poll_s,
                  body=_body(ctx.variant, symbols, readings))


def _cell(symbol: str, reading) -> str:
    """One quote as three stacked lines: what a markets cell is made of."""
    pct = reading.get("change_pct")
    direction = "" if pct is None else ("up" if pct >= 0 else "down")
    delta = "" if pct is None else f"{abs(float(pct)):.2f}%"
    return (f'<div class="q"><div class="sym">{symbol}</div>'
            f'<div class="px">{_fmt_price(reading.get("price"))}</div>'
            f'<div class="ch">{_icons.arrow(direction, 10)}'
            f'<span>{delta}</span></div></div>')


def _body(variant: str, symbols, readings) -> str:
    """The arrangement for this SHAPE.

    `stacked` used to decide this and reached only the round panel; the HTML
    was a `<table>` at every size, so a 117x62 markets cell got a three-column
    table with a 6em change column inside 120px of usable width and ran
    `AAPL228.40` together.
    """
    if not symbols:
        return f'<div class="wrap">{empty("sin símbolos", "añade alguno en los ajustes", variant)}</div>'

    if variant == "badge":
        # ONE symbol. A cell that rotates is a cell you cannot read at a
        # glance, and the band already has five more of them.
        return f'<div class="wrap badge">{_cell(symbols[0], readings[symbols[0]])}</div>'

    if variant == "strip":
        # Every symbol along the band, evenly spread rather than huddled in
        # the leftmost 200px.
        inner = "".join(_cell(s, readings[s]) for s in symbols)
        return f'<div class="wrap strip">{inner}</div>'

    rows = "".join(
        f'<div class="row"><div class="sym">{s}</div>'
        f'<div class="px">{_fmt_price(readings[s].get("price"))}</div>'
        f'<div class="ch">{_fmt_change(readings[s].get("change_pct"))[0]}</div>'
        f'</div>' for s in symbols)
    return f'<div class="wrap list">{rows}</div>' 


def _scene(ctx, w, h, instructions, *, poll_s, body) -> Scene:
    return Scene(layout="fill",
                 components=({"c": "quotes", "draw": instructions},),
                 poll_s=poll_s, poll_max_s=300,
                 html=page(w, h, body, CSS, shape=ctx.variant))


CSS = """
.wrap{padding:var(--pad);height:100%;display:flex;align-items:center}

/* A quote as three lines. Tabular numerals so a column of prices lines up on
   the decimal rather than wandering. */
.q{min-width:0;text-align:left}
.q .sym{font-size:var(--xs);font-weight:500;letter-spacing:.03em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.q .px{font-size:var(--lg);font-weight:500;font-variant-numeric:tabular-nums;
  line-height:1.15}
.q .ch{display:flex;align-items:center;gap:2px;font-size:var(--xs)}
.ar{flex:none;display:block}

/* The three lines are one block, centred in the cell, not floating apart. */
.wrap.badge{align-items:center;justify-content:flex-start}
.wrap.badge .q{width:100%}
/* Evenly spread along the band, not huddled at the left. */
.wrap.strip{gap:var(--pad)}
.wrap.strip .q{flex:1}

/* A list. No rule under each row: the design separates these with whitespace
   and a weight change, and a black hairline under every line reads as a
   ledger. */
.wrap.list{flex-direction:column;align-items:stretch;justify-content:center}
.row{display:flex;align-items:baseline;gap:var(--pad-sm);
  padding:var(--pad-sm) 0;font-size:var(--fs)}
.row + .row{border-top:1px dotted #000}
.row .sym{font-weight:500;min-width:0;overflow:hidden;text-overflow:ellipsis}
.row .px{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:500}
.row .ch{font-size:var(--sm);width:5.4em;text-align:right}
td{padding:var(--pad-sm) 0}
.sym{font-weight:600}
.px{text-align:right;font-variant-numeric:tabular-nums}
.ch{text-align:right;width:6em;font-size:var(--fs)}
""" + EMPTY_CSS
