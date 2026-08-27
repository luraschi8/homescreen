"""The week grid: what this screen shows, and when.

A schedule is the one thing here that is genuinely better seen than read. Seven
rows of twenty-four cells, each painted with the view that wins there, answers
"what is on at 3pm on Sunday" and "did those two slots overlap the way I meant"
in one glance -- questions that a list of from/to fields makes you simulate in
your head.

Overlap precedence is the reason this exists rather than a nicety. The rule is
"the last matching slot wins", which is one sentence and still hard to hold
when four slots interleave. The grid shows the answer instead of asking anyone
to derive it.
"""

from __future__ import annotations

from homescreen import schedule as scheduling
from homescreen.web.layout import e

DAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

#: Distinct enough to tell apart at a glance, and legible in both themes.
SWATCHES = ("#2563eb", "#0a7d33", "#b3261e", "#8a5a00", "#6d28d9",
            "#0e7490", "#be185d", "#4d7c0f")

CSS = """
.week{border-collapse:collapse;width:100%;font-size:.68rem;table-layout:fixed}
.week th{font-weight:600;color:var(--dim);padding:.2rem;text-align:center}
.week th.d{text-align:right;padding-right:.5rem;width:2.6rem}
.week td{height:1.35rem;border:1px solid var(--panel);padding:0}
.week td span{display:block;height:100%;border-radius:2px}
.week td.now span{outline:2px solid var(--fg);outline-offset:-2px}
.legend{display:flex;gap:.8rem;flex-wrap:wrap;margin-top:.7rem;font-size:.8rem}
.legend b{display:inline-block;width:.7rem;height:.7rem;border-radius:2px;
  margin-right:.35rem;vertical-align:-1px}
.slot{display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:.5rem;
  align-items:end;margin-bottom:.5rem}
.slot .days{display:flex;gap:.3rem;flex-wrap:wrap}
.slot .days label{display:flex;align-items:center;gap:.2rem;font-size:.72rem;
  color:var(--dim)}
.slot .days input{width:auto}
"""


def _colours(views) -> dict:
    return {name: SWATCHES[i % len(SWATCHES)]
            for i, name in enumerate(sorted(views))}


def week_grid(plan: dict, views, now: float) -> str:
    """7x24 of whatever wins there, with the current hour outlined.

    Sampled on the hour. A slot that starts at 09:30 therefore paints from 09,
    which is the honest resolution for a cell an hour wide -- the grid is for
    seeing shape and overlap, and the fields below it carry the minutes.
    """
    import datetime as _dt
    colours = _colours(views)
    zone = scheduling._zone(plan.get("tz"))
    current = _dt.datetime.fromtimestamp(now, zone)
    # Monday of this week, so the grid is a week the operator is actually in.
    monday = (current - _dt.timedelta(days=current.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)

    head = "".join(f"<th>{h:02d}</th>" for h in range(24))
    rows = []
    for index, label in enumerate(DAYS):
        cells = []
        for hour in range(24):
            moment = monday + _dt.timedelta(days=index, hours=hour)
            winner = scheduling.active_view(plan, moment.timestamp())
            colour = colours.get(winner, "var(--line)")
            here = (moment.isoweekday() == current.isoweekday()
                    and hour == current.hour)
            cells.append(f'<td class="{"now" if here else ""}" '
                         f'title="{e(label)} {hour:02d}:00 — {e(winner)}">'
                         f'<span style="background:{colour}"></span></td>')
        rows.append(f'<tr><th class="d">{e(label)}</th>{"".join(cells)}</tr>')

    legend = "".join(
        f'<span><b style="background:{colours[name]}"></b>{e(name)}</span>'
        for name in sorted(views))
    return (f'<table class="week"><tr><th class="d"></th>{head}</tr>'
            f'{"".join(rows)}</table><div class="legend">{legend}'
            f'<span class="hint">el recuadro marca la hora actual</span></div>')


def slot_fields(index: int, slot: dict, views) -> str:
    """One slot as editable fields."""
    picked = set(slot.get("days") or ())
    days = "".join(
        f'<label><input type="checkbox" name="slot{index}.day" '
        f'value="{n}"{" checked" if n in picked else ""}>{e(DAYS[n - 1])}</label>'
        for n in range(1, 8))
    options = "".join(
        f'<option value="{e(v)}"{" selected" if v == slot.get("view") else ""}>'
        f'{e(v)}</option>' for v in sorted(views))
    return f"""<div class="slot">
  <label class="field">Muestra
    <select name="slot{index}.view">{options}</select></label>
  <label class="field">Desde
    <input type="time" name="slot{index}.from"
      value="{e(slot.get("from") or "09:00")}"></label>
  <label class="field">Hasta
    <input type="time" name="slot{index}.to"
      value="{e(slot.get("to") or "23:00")}"></label>
  <label class="check"><input type="checkbox" name="slot{index}.remove">
    quitar</label>
  <div class="days" style="grid-column:1/-1">{days}</div>
</div>"""


def editor(hw: str, plan: dict, views, now: float) -> str:
    """The whole section: the grid, the slots, and one empty slot to add."""
    plan = plan if isinstance(plan, dict) else {}
    slots = list(plan.get("slots") or ())
    default = plan.get("default") or (sorted(views)[0] if views else "")
    picks = "".join(
        f'<option value="{e(v)}"{" selected" if v == default else ""}>{e(v)}'
        f'</option>' for v in sorted(views))
    rows = "".join(slot_fields(i, s, views) for i, s in enumerate(slots))
    rows += slot_fields(len(slots), {"view": default, "from": "09:00",
                                     "to": "23:00", "days": []}, views)
    return f"""<h2>Horario</h2>
<div class="panel"><div class="pad">
  {week_grid(plan, views, now)}
  <form class="stack" method="post" action="/device/{e(hw)}/schedule"
        style="margin-top:1.2rem">
    <label class="field">Por defecto
      <select name="default">{picks}</select>
      <span class="hint">Lo que se muestra fuera de cualquier franja. Nunca
        se queda en blanco.</span></label>
    {rows}
    <div class="actions"><button type="submit">Guardar horario</button></div>
  </form>
</div></div>"""
