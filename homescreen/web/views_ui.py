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
.p-opts{margin:.35rem 0 .8rem 0;padding-left:.8rem;
  border-left:2px solid var(--line-soft)}
.p-opts label.field{max-width:100%}
.view{border:1px solid var(--line);border-radius:9px;padding:.9rem 1rem;
  margin-bottom:.8rem}
.view h3{margin:0 0 .7rem;font-size:.9rem;font-weight:650}
.slot-row{display:grid;grid-template-columns:9rem 1fr;gap:.6rem;
  align-items:center;margin-bottom:.5rem}
.slot-row .rg{font-size:.8rem;color:var(--dim);font-family:var(--mono)}
.slot-row .cap{font-size:.7rem;color:var(--faint)}
"""

EMPTY = "—"


def _placement_options(view_name: str, region: str, index: int,
                       component: str, schemas: dict, values: dict) -> str:
    """One placement's own settings.

    This is what makes two calendars on one screen two different calendars.
    The options were always per placement in the record; without fields here
    the only way to differ was the API, so in practice every placement of a
    component shared one configuration.
    """
    from .fields import field
    schema = (schemas or {}).get(component) or []
    if not component or not schema:
        return ""
    rendered = []
    for spec in schema:
        key = spec.get("key")
        one = field(spec, (values or {}).get(key, spec.get("default")))
        # Namespaced by the slot's coordinates, so two placements of the same
        # component post two independent sets rather than colliding on
        # `opt.url`.
        rendered.append(one.replace(
            f'name="opt.{key}"',
            f'name="o.{view_name}.{region}.{index}.{key}"'))
    return (f'<div class="p-opts">{"".join(rendered)}</div>')


def _region_row(view_name: str, region: str, spec: dict, chosen: list,
                offered, schemas=None, options=None) -> str:
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
            f'<select name="v.{e(view_name)}.{e(region)}.{index}">{picks}</select>'
            + _placement_options(view_name, region, index, current, schemas,
                                 (options or {}).get((region, index)) or {}))
    return (f'<div class="slot-row"><span class="rg">{e(region)}'
            f'<span class="cap"><br>{w}&times;{h}</span></span>'
            f'<div>{"".join(rows)}</div></div>')


def editor(hw: str, views: dict, regions: dict, offered, template: str,
           schemas=None) -> str:
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
        values: dict = {}
        for placement in placements:
            region = placement.get("region")
            slot = len(by_region.setdefault(region, []))
            by_region[region].append(placement.get("component"))
            values[(region, slot)] = placement.get("options") or {}
        rows = "".join(
            _region_row(view_name, region, spec, by_region.get(region, []),
                        offered, schemas, values)
            for region, spec in regions.items())
        blocks.append(f'<div class="view"><h3>{e(view_name)}</h3>{rows}</div>')

    return f"""<h2>Qué contiene cada vista</h2>
<div class="panel"><div class="pad">
  <p class="empty" style="margin-top:0">Distribución <strong>{e(template)}</strong>.
  Cada región muestra su tamaño real en píxeles. Deja «{EMPTY}» para vaciarla.
  Cada hueco tiene sus propios ajustes: dos calendarios en una pantalla son dos
  calendarios distintos.</p>
  <form class="stack" method="post" action="/device/{e(hw)}/views">
    {"".join(blocks)}
    <label class="field">Añadir una vista
      <input type="text" name="new_view" maxlength="40"
        placeholder="p. ej. mañana">
      <span class="hint">Se crea vacía y se rellena aquí mismo.</span></label>
    <div class="actions"><button type="submit">Guardar vistas</button></div>
  </form>
</div></div>"""


def parse(form, regions: dict, view_names, schemas=None) -> dict:
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
                prefix = f"o.{view_name}.{region}.{index}."
                raw = {k[len(prefix):]: v for k, v in form.items()
                       if k.startswith(prefix)}
                placements.append({
                    "id": f"{view_name}-{region}-{index}",
                    "region": region, "component": component,
                    # None when this slot posted no fields at all, so a
                    # component with no options keeps whatever it had rather
                    # than being reset to empty.
                    "options": raw or None})
        views[view_name] = {"placements": placements}
    return views
