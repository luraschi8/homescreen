"""What is coming up.

The clearest case for surface adaptation: a wide panel shows the next several
events as a list, and a small round one shows the next one — because the next
one is the answer to the question you ask a small screen.
"""

from __future__ import annotations

from datetime import datetime

from homescreen import draw
from homescreen.reading import Reading
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import EMPTY_CSS, empty, page

#: DISJOINT; see `weather.SURFACES` for why the maximums are what make that
#: true rather than the order.
SURFACES = (
    {"variant": "strip", "at": (764, 62),
     "min_w": 200, "min_h": 24, "max_h": 110, "min_aspect": 4.0},
    {"variant": "badge", "at": (127, 62),
     "min_w": 90, "min_h": 40, "max_h": 110, "max_aspect": 4.0},
    # v6's AGENDA block is 417x104 under its heading: four rows.
    {"variant": "card", "at": (417, 150),
     "min_short": 90, "min_h": 111, "max_h": 239},
    {"variant": "panel", "at": (417, 335),
     "min_short": 90, "min_w": 200, "min_h": 240},
)

OPTIONS = (
    {"key": "url", "label": "URL del calendario (.ics)", "type": "text",
     "default": "",
     "help": "La dirección privada de Google/Apple. Sólo la ve el servidor."},
    {"key": "days", "label": "Días por delante", "type": "int", "default": 14},
    {"key": "max_events", "label": "Cuántos mostrar", "type": "int",
     "default": 4, "help": "En pantallas pequeñas siempre se muestra uno."},
)

#: A calendar changes on human time. Five minutes is well inside the cadence a
#: person notices, and every poll is a 304 unless something moved.
POLL_S = 300

WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic")


def needs(options: dict, cfg: dict) -> tuple:
    url = str((options or {}).get("url") or "").strip()
    if not url:
        return ()
    try:
        days = int((options or {}).get("days") or 14)
    except (TypeError, ValueError):
        days = 14
    return ({"provider": "ics", "params": {"url": url, "days": days}},)


def _when(stamp: str, now: float) -> str:
    """A time a person reads at a glance: 'hoy 14:00', 'mañana', 'jue 9:00'."""
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return ""
    today = datetime.fromtimestamp(now, moment.tzinfo).date()
    delta = (moment.date() - today).days
    clock = moment.strftime("%H:%M")
    if delta == 0:
        return f"hoy {clock}"
    if delta == 1:
        return f"mañana {clock}"
    if 0 < delta < 7:
        return f"{WEEKDAYS[moment.weekday()]} {clock}"
    return f"{moment.day} {MONTHS[moment.month - 1]} {clock}"


def build(ctx: SceneContext) -> Scene:
    options = ctx.options or {}
    wanted = needs(options, ctx.cfg)
    reading = (ctx.data(wanted[0]) if wanted and callable(ctx.data) else None)
    reading = reading if reading is not None else Reading.nothing()
    events = [e for e in (reading.get("events") or ()) if isinstance(e, dict)]

    w = int(ctx.caps.get("w") or 240)
    h = int(ctx.caps.get("h") or 240)
    try:
        wanted_count = int(options.get("max_events") or 4)
    except (TypeError, ValueError):
        wanted_count = 4
    shape = str(ctx.caps.get("shape") or "rect")
    lines = [f"{_when(e.get('when'), ctx.now)}  {str(e.get('summary') or '')}"
             for e in events[:wanted_count]]
    # How many rows this glass can actually SHOW -- measured against the lines
    # themselves, not against the panel's size class. Dropping rows until they
    # fit is what turns "too small for a list" into "shows the next one".
    rows = len(lines)
    while rows > 1 and not draw.lines_fit(lines[:rows], w, h, shape=shape):
        rows -= 1

    if not wanted:
        instructions = [draw.text("center", "sin calendario", "sm", "dim"),
                        draw.text("below", "añade una URL .ics", "xs", "dim")]
    elif not events:
        instructions = [draw.text("center", "nada a la vista", "md"),
                        draw.text("below", f"próximos {options.get('days', 14)} días",
                                  "xs", "dim")]
    elif rows == 1:
        # The next one, which is what you ask a small screen. Today is warned
        # rather than dimmed: the difference between "later" and "soon" is the
        # only thing you need from across a room.
        first = events[0]
        when = _when(first.get("when"), ctx.now)
        instructions = [draw.text("above", when, "sm",
                                  "warn" if when.startswith("hoy") else "accent"),
                        draw.text("center", str(first.get("summary") or ""), "md")]
        if len(events) > 1:
            instructions.append(
                draw.text("rim_bottom", f"+{len(events) - 1} más", "xs", "dim"))
    else:
        slots = ("rim_top", "above", "center", "below", "rim_bottom")
        instructions = [
            draw.text(slot, f"{_when(e.get('when'), ctx.now)}  "
                            f"{str(e.get('summary') or '')}", "sm",
                      "warn" if _when(e.get("when"), ctx.now).startswith("hoy")
                      else "normal")
            for e, slot in zip(events[:rows], slots)]

    # As many as the region holds, not eight. The HTML hard-coded a count
    # while `build` computed a fit for the DRAW list and used neither -- so a
    # 318px block was handed eight rows and clipped the last one through its
    # x-height, which is worse than dropping it.
    shown = events[:max(1, ctx.rows)] if ctx.variant in ("card", "panel") \
        else events[:1]
    body = "".join(
        f'<div class="row"><div class="w">{_when(e.get("when"), ctx.now)}</div>'
        f'<div class="s">{str(e.get("summary") or "")}</div></div>'
        for e in shown)
    # The same thing the draw list says. An empty table is a 417x335 hole in
    # the dashboard with nothing to explain it, while the identical component
    # on the round panel said "sin calendario" -- one component cannot be
    # forthcoming on one screen and silent on another.
    if not body:
        note = ("sin calendario" if not wanted else "nada a la vista")
        hint = ("a\u00f1ade una URL .ics" if not wanted
                else f"pr\u00f3ximos {options.get('days', 14)} d\u00edas")
        body_html = empty(note, hint, ctx.variant)
    else:
        body_html = f'<table>{body}</table>'
    return Scene(layout="fill",
                 components=({"c": "calendar", "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S,
                 html=page(w, h, f'<div class="wrap">{body_html}</div>', CSS,
                           shape=ctx.variant))


CSS = """
.wrap{padding:var(--pad);height:100%}
/* A fixed time column and a weight change carry the rows. The design's own
   separators are #ececec, which thresholds to nothing at 1-bit, so a solid
   black rule under every line is not a translation of it -- it is a ledger. */
.list{display:flex;flex-direction:column;justify-content:center;height:100%}
.row{display:flex;gap:var(--pad-sm);align-items:baseline;
  padding:var(--pad-sm) 0;font-size:var(--fs)}
.row .w{width:3.2em;flex:none;font-weight:500;font-size:var(--sm)}
.row .s{min-width:0;flex:1;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
""" + EMPTY_CSS
