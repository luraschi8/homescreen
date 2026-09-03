"""The shop's day: what it has taken, and whether that is going well.

Two numbers do the work. The takings answer "how are we doing", and the
comparison with yesterday-to-this-same-minute answers "compared with what" --
which is the question the first number always provokes and cannot answer on
its own.
"""

from __future__ import annotations

from homescreen import draw
from homescreen.reading import Reading
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._icons import arrow
from homescreen.scenes._style import EMPTY_CSS, empty, esc, page

#: Takings read at any size: at the smallest it is one number, and the
#: supporting figures are added as the room appears.
SURFACES = (
    {"variant": "strip", "at": (764, 62),
     "min_w": 200, "min_h": 24, "max_h": 80, "min_aspect": 6.0},
    {"variant": "badge", "at": (127, 62),
     "min_w": 90, "max_w": 199, "min_h": 40, "max_h": 80},
    {"variant": "card", "at": (417, 150),
     "min_w": 90, "min_h": 81, "max_h": 239},
    {"variant": "panel", "at": (321, 335), "min_w": 90, "min_h": 240},
)

OPTIONS = (
    {"key": "shop", "label": "Dominio de la tienda", "type": "text",
     "default": "", "placeholder": "mi-tienda.myshopify.com",
     "help": "El dominio .myshopify.com. El token va en «credenciales»."},
    {"key": "show_compare", "label": "Comparar con ayer", "type": "bool",
     "default": True,
     "help": "Ayer a esta misma hora, no el día entero: si no, por la mañana "
             "siempre parece que vas perdiendo."},
    {"key": "show_pending", "label": "Mostrar pedidos sin enviar",
     "type": "bool", "default": True},
)

#: A shop owner looks at this often and orders arrive all day, but the panel
#: takes 3.7s to redraw. Five minutes is what the fetcher already uses.
POLL_S = 300

#: How much of the block the takings may take. It is one number with a short
#: label under it, like the clock.
_HERO_SHARE = {"card": 0.44, "panel": 0.30, "badge": 0.42, "strip": 0.0}

_SYMBOL = {"EUR": "€", "USD": "$", "GBP": "£"}


def needs(options: dict, cfg: dict) -> tuple:
    shop = str((options or {}).get("shop") or "").strip()
    if not shop:
        return ()
    return ({"provider": "shopify", "params": {"shop": shop}},)


def money(value, currency: str = "", decimals: int = 0) -> str:
    """A sum, in the Spanish convention: 1.234 € rather than €1,234.

    Whole euros for a total, because the cents on a day's takings are noise at
    a glance; two places for an average, where they are the difference between
    two baskets.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    text = f"{number:,.{decimals}f}".replace(",", " ").replace(".", ",")
    text = text.replace(" ", ".")
    symbol = _SYMBOL.get(str(currency or "").upper(), str(currency or ""))
    return f"{text} {symbol}".strip()


def delta(now, before):
    """`(direction, text)` against yesterday, or `("", "")` if it says nothing.

    Silent when yesterday was zero: "up from nothing" is division by zero
    dressed as insight, and a shop's first order of the day would read +∞.
    """
    try:
        now, before = float(now), float(before)
    except (TypeError, ValueError):
        return "", ""
    if before <= 0:
        return "", ""
    change = (now - before) / before * 100.0
    return ("up" if change >= 0 else "down"), f"{abs(change):.0f}%"


def build(ctx: SceneContext) -> Scene:
    options = ctx.options or {}
    wanted = needs(options, ctx.cfg)
    reading = ctx.data(wanted[0]) if wanted and callable(ctx.data) else None
    reading = reading if reading is not None else Reading.nothing()

    w = int(ctx.caps.get("w") or 240)
    h = int(ctx.caps.get("h") or 240)
    currency = reading.get("currency") or ""
    total = reading.get("total")
    count = reading.get("orders")

    if not wanted:
        note, hint = "sin tienda", "añade el dominio"
    elif reading.missing:
        note, hint = "sin datos", "revisa el token"
    else:
        note = hint = ""

    if note:
        instructions = [draw.text("center", note, "md"),
                        draw.text("below", hint, "xs", "dim")]
        body = empty(note, hint, ctx.variant)
        return Scene(layout="fill", poll_s=POLL_S, poll_max_s=POLL_S,
                     components=({"c": "shopify", "draw": instructions},),
                     html=page(w, h, f'<div class="wrap">{body}</div>', CSS,
                               shape=ctx.variant))

    takings = money(total, currency)
    # The shop names the figure. In the band that makes the label exactly what
    # a ticker's is -- the source, not the metric -- and on the round face
    # there is no section heading, so without it "870 EUR" is a number with
    # nothing saying whose. Falls back to a word when the shop has no name.
    # ESCAPED: the name comes from Shopify, so it is feed text like any other
    # and reaches a document Chromium rasterises.
    title = esc(str(reading.get("shop") or "").strip().upper()) or "VENTAS"
    direction, change = delta(total, reading.get("prev_total")) \
        if options.get("show_compare", True) else ("", "")
    orders_line = f"{count} pedido{'' if count == 1 else 's'}"

    # One big figure and one line under it. A round face has a single place the
    # eye lands, and a third number competes for it rather than adding to it.
    instructions = [draw.text("above", title, "xs", "dim"),
                    draw.text("center", takings, "xl", "accent"),
                    draw.text("below", orders_line, "sm", "dim")]

    return Scene(layout="fill", poll_s=POLL_S, poll_max_s=POLL_S,
                 components=({"c": "shopify", "draw": instructions},),
                 html=page(w, h, _body(ctx, reading, takings, orders_line,
                                       direction, change, currency, title),
                           CSS, shape=ctx.variant,
                           hero_share=_HERO_SHARE.get(ctx.variant)))


def _body(ctx, reading, takings, orders_line, direction, change, currency,
          title):
    options = ctx.options or {}
    variant = ctx.variant
    change_html = (f'<span class="chg">{arrow(direction, 10)}'
                   f'<span>{change}</span></span>') if change else ""

    if variant == "strip":
        parts = [takings, orders_line]
        if change:
            parts.append(f"{change} vs ayer")
        return (f'<div class="wrap row"><span class="sh-sym">{title}</span>'
                f'<span class="big">{takings}</span>'
                f'<span class="sub">{orders_line}</span>{change_html}</div>')

    if variant == "badge":
        # A cell of the markets band, so it keeps that band's rhythm: a small
        # label, the number, a small line under it -- the same three lines as a
        # ticker beside it, at the same sizes. A cell that invents its own
        # shape reads as a mistake in a row of five that agree.
        return (f'<div class="wrap badge"><div class="q">'
                f'<div class="sh-sym">{title}</div>'
                f'<div class="sh-px">{takings}</div>'
                f'<div class="sh-ch">{orders_line}</div></div></div>')

    # A block: the takings, then the figures that qualify them.
    rows = []
    if change:
        rows.append(("vs ayer", f'{arrow(direction, 11)}'
                                f'<span>{change}</span>'))
    rows.append(("media", money(reading.get("average"), currency, 2)))
    if options.get("show_pending", True):
        pending = reading.get("unfulfilled")
        if pending:
            rows.append(("sin enviar", str(pending)))
    # The shop's name is not shown. There is one shop, its owner is reading,
    # and the composer already puts a heading on the block -- so it was a row
    # of clutter under the numbers that matter.
    lines = "".join(f'<div class="ln"><span class="k">{k}</span>'
                    f'<span class="v">{v}</span></div>' for k, v in rows)
    return (f'<div class="wrap"><div class="head">'
            f'<div class="sh-title">{title}</div>'
            f'<div class="big">{takings}</div>'
            f'<div class="sh-lab">{orders_line} · hoy</div></div>'
            f'<div class="rows">{lines}</div></div>')


CSS = """
.wrap{padding:var(--pad);height:100%;display:flex;flex-direction:column;
  justify-content:center}
.big{font-size:var(--hero);font-weight:600;line-height:1;
  letter-spacing:-.02em}
/* `sh-` prefixed: `.lab` is a BASE_CSS class, and a scene that
   redefines it reaches every other component on the same composed
   page -- `compose.scope_css` isolates a fragment's selectors, not
   the shared ones it overrides. */
/* Above the number, so the block says whose money this is before it says how
   much. Small: it is a caption, and the figure is the reason to look. */
.sh-title{font-size:var(--xs);letter-spacing:.14em;text-transform:uppercase;
  font-weight:500;margin-bottom:1px}
.sh-lab{font-size:var(--xs);letter-spacing:.06em;
  text-transform:uppercase;margin-top:2px}
/* A markets cell: the same three lines, at the same sizes, as the tickers it
   sits beside. */
.wrap.badge{flex-direction:column;align-items:flex-start;
  justify-content:center}
.badge .q{min-width:0;width:100%;text-align:left}
.sh-sym{font-size:var(--xs);font-weight:500;letter-spacing:.03em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sh-px{font-size:var(--lg);font-weight:500;font-variant-numeric:tabular-nums;
  line-height:1.15}
.sh-ch{font-size:var(--xs);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.wrap.row{flex-direction:row;align-items:baseline;gap:.7em}
.wrap.row .big{font-size:var(--lg)}
.wrap.row .sub{font-size:var(--fs)}
.chg{display:inline-flex;align-items:center;gap:3px;font-size:var(--sm)}
/* The qualifying figures, right-aligned so the numbers form a column. */
.rows{margin-top:var(--pad-sm)}
.ln{display:flex;align-items:baseline;height:var(--row-tight);
  font-size:var(--fs)}
.ln .k{font-size:var(--sm)}
.ln .v{margin-left:auto;font-weight:500;display:inline-flex;
  align-items:center;gap:3px}
""" + EMPTY_CSS
