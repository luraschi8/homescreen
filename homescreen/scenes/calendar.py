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
from homescreen.scenes._style import page

SURFACES = ({"min_short": 90},)

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
        # The next one, which is what you ask a small screen.
        first = events[0]
        instructions = [draw.text("above", _when(first.get("when"), ctx.now),
                                  "sm", "dim"),
                        draw.text("center", str(first.get("summary") or ""), "md")]
        if len(events) > 1:
            instructions.append(
                draw.text("rim_bottom", f"+{len(events) - 1} más", "xs", "dim"))
    else:
        slots = ("rim_top", "above", "center", "below", "rim_bottom")
        instructions = [
            draw.text(slot, f"{_when(e.get('when'), ctx.now)}  "
                            f"{str(e.get('summary') or '')}", "sm")
            for e, slot in zip(events[:rows], slots)]

    body = "".join(
        f'<tr><td class="w">{_when(e.get("when"), ctx.now)}</td>'
        f'<td>{str(e.get("summary") or "")}</td></tr>' for e in events[:8])
    return Scene(layout="fill",
                 components=({"c": "calendar", "draw": instructions},),
                 poll_s=POLL_S, poll_max_s=POLL_S,
                 html=page(w, h, f'<div class="wrap"><table>{body}</table></div>',
                           CSS))


CSS = """
.wrap{padding:18px;height:100%}
table{width:100%;border-collapse:collapse;font-size:22px}
td{padding:7px 0;border-bottom:1px solid #000;vertical-align:top}
.w{width:9em;font-weight:600}
"""
