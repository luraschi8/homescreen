"""Editing what a view contains: which component sits in which region.

The schedule editor decides WHEN a view shows. This decides WHAT is in it, and
it is the piece that makes a composed dashboard configurable from a web page
rather than from curl.

Only shown for glass that has more than one region. A screen with one region
already has this: it is the "Qué muestra" form, and rendering a second, more
elaborate way to say the same thing would be two places to change one fact.
"""

from __future__ import annotations

from homescreen.web.layout import e

CSS = """
.view{border:1px solid var(--line);border-radius:9px;padding:.9rem 1rem;
  margin-bottom:.8rem}
.view h3{margin:0 0 .7rem;font-size:.9rem;font-weight:650}
.slot-row{display:grid;grid-template-columns:9rem 1fr;gap:.6rem;
  align-items:center;margin-bottom:.5rem}
.slot-row .rg{font-size:.8rem;color:var(--dim);font-family:var(--mono)}
.slot-row .cap{font-size:.7rem;color:var(--faint)}
"""

EMPTY = "—"


def _region_row(view_name: str, region: str, spec: dict, chosen: list,
                offered) -> str:
    """One region, and what is in it.

    A region that holds several gets several selects, because "add another" and
    "remove" are the same control when an empty slot means empty: choosing the
    dash removes it, and the row below is where the next one goes.
    """
    x, y, w, h = spec["rect"]
    rows = []
    for index in range(spec["holds"]):
        current = chosen[index] if index < len(chosen) else ""
        picks = f'<option value="">{EMPTY}</option>' + "".join(
            f'<option value="{e(name)}"{" selected" if name == current else ""}'
            f'{"" if ok else " disabled"}>{e(name)}'
            f'{"" if ok else " — " + e(why)}</option>'
            for name, ok, why in offered)
        rows.append(
            f'<select name="v.{e(view_name)}.{e(region)}.{index}">{picks}</select>')
    return (f'<div class="slot-row"><span class="rg">{e(region)}'
            f'<span class="cap"><br>{w}&times;{h}</span></span>'
            f'<div>{"".join(rows)}</div></div>')


def editor(hw: str, views: dict, regions: dict, offered, template: str) -> str:
    """Every view on this screen, and what each holds.

    Options are NOT edited here. A placement's settings belong to the component
    and are already edited above; putting a second copy beside the arrangement
    would be two forms writing one value, which is how they come to disagree.
    """
    if len(regions) < 2:
        return ""
    blocks = []
    for view_name in sorted(views):
        placements = (views[view_name] or {}).get("placements") or []
        by_region: dict = {}
        for placement in placements:
            by_region.setdefault(placement.get("region"), []).append(
                placement.get("component"))
        rows = "".join(
            _region_row(view_name, region, spec, by_region.get(region, []),
                        offered)
            for region, spec in regions.items())
        blocks.append(f'<div class="view"><h3>{e(view_name)}</h3>{rows}</div>')

    return f"""<h2>Qué contiene cada vista</h2>
<div class="panel"><div class="pad">
  <p class="empty" style="margin-top:0">Distribución <strong>{e(template)}</strong>.
  Cada región muestra su tamaño real en píxeles. Deja «{EMPTY}» para vaciarla.</p>
  <form class="stack" method="post" action="/device/{e(hw)}/views">
    {"".join(blocks)}
    <label class="field">Añadir una vista
      <input type="text" name="new_view" maxlength="40"
        placeholder="p. ej. mañana">
      <span class="hint">Se crea vacía y se rellena aquí mismo.</span></label>
    <div class="actions"><button type="submit">Guardar vistas</button></div>
  </form>
</div></div>"""


def parse(form, regions: dict, view_names) -> dict:
    """Rebuild every view's placements from the posted selects.

    The whole arrangement, not a patch: the form shows every slot on the screen
    at once, so what it posts IS the arrangement, and reconstructing it whole
    means an empty select genuinely empties a region rather than being read as
    "no opinion".
    """
    views: dict = {}
    for view_name in view_names:
        placements = []
        for region, spec in regions.items():
            for index in range(spec["holds"]):
                component = (form.get(f"v.{view_name}.{region}.{index}")
                             or "").strip()
                if not component:
                    continue
                placements.append({
                    "id": f"{view_name}-{region}-{index}",
                    "region": region, "component": component})
        views[view_name] = {"placements": placements}
    return views
