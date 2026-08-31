"""Editing what a view contains: which component sits in which region.

The schedule editor decides WHEN a view shows. This decides WHAT is in it, and
it is the piece that makes a composed dashboard configurable from a web page
rather than from curl.

Only shown for glass that has more than one region. A screen with one region
already has this: it is the "Qué muestra" form, and rendering a second, more
elaborate way to say the same thing would be two places to change one fact.
"""

from __future__ import annotations

import re

#: Ends the attribute, or the field key's own separator. Everything else is
#: somebody's language.
_UNSAFE_IN_NAME = re.compile(r"""[<>&"'.\\/]+""")

from homescreen import layout
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

/* The panel, to scale. Boxes are positioned as percentages of the real
   rectangles, so the picture cannot drift from the geometry it describes. */
.map{position:relative;width:100%;max-width:34rem;margin:0 0 1rem;
  background:var(--bg);border:1px solid var(--line);border-radius:6px;
  overflow:hidden}
.mslot{position:absolute;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:.1rem;overflow:hidden;
  box-sizing:border-box;border:1px dashed var(--line);border-radius:3px;
  background:var(--panel);cursor:pointer;padding:2px;text-align:center;
  transition:background .12s,border-color .12s}
.mslot:hover{border-color:var(--accent)}
.mslot.filled{border-style:solid;border-color:var(--accent)}
.mslot .mn{font-size:.68rem;font-weight:650;line-height:1.1;
  color:var(--faint);overflow:hidden;text-overflow:ellipsis;max-width:100%}
.mslot.filled .mn{color:var(--fg)}
/* The region name is context, not content: it shows when there is room. */
.mslot .mr{font-size:.58rem;color:var(--faint);font-family:var(--mono);
  line-height:1;overflow:hidden;max-width:100%}
@media (max-width:640px){.mslot .mr{display:none}}
"""

VACANT = "vacío"
EMPTY = "—"


def safe_view_name(raw) -> str:
    """A view name narrowed to what cannot break the page that renders it.

    View names are operator input that becomes a `name="o.<view>.<region>
    .<index>.<key>"` attribute and the key the parser splits back out. Only the
    structurally dangerous characters go: the quotes and angle brackets that
    would end the attribute, and the `.` that separates the field key's parts.

    Everything else stays, accents included. The UI is Spanish and `mañana` is
    not a name anybody typed -- folding to ASCII would trade an injection bug
    for a legibility one.
    """
    cleaned = _UNSAFE_IN_NAME.sub("-", str(raw or "")).strip("- ")[:40]
    return cleaned or "vista"


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


def _for_slot(offered, slot_caps: dict, fits):
    """The offered list, re-judged against one slot's measured size.

    `fits(name, caps) -> (ok, why)` is injected rather than imported so this
    module keeps knowing only about HTML. Without it the list passes through
    unchanged, which is what the round panel's single full-bleed region wants.
    """
    if fits is None:
        return offered
    out = []
    for name, ok, why in offered:
        if not ok:
            out.append((name, ok, why))            # already refused, for a
            continue                               # better reason than size
        here_ok, here_why = fits(name, slot_caps)
        out.append((name, here_ok, here_why if not here_ok else why))
    return out


def _region_row(view_name: str, region: str, spec: dict, chosen: list,
                offered, schemas=None, options=None, fits=None) -> str:
    """One region, and what is in it.

    A region that holds several gets several selects, because "add another" and
    "remove" are the same control when an empty slot means empty: choosing the
    dash removes it, and the row below is where the next one goes.
    """
    x, y, w, h = spec["rect"]
    rows = []
    for index in range(spec["holds"]):
        current = chosen[index] if index < len(chosen) else ""
        # Judged at the size this slot would ACTUALLY get, not the region's.
        # A region holding four divides four ways, so offering a component
        # against the whole rectangle tells the operator it fits and then
        # renders it into a quarter of that. Slot `index` is measured as the
        # last one filled, which is what happens when they are filled in order.
        here = layout.slots(spec, index + 1)[index] if index else spec["rect"]
        slot_caps = {"w": here[2], "h": here[3]}
        picks = f'<option value="">{EMPTY}</option>' + "".join(
            f'<option value="{e(name)}"{" selected" if name == current else ""}'
            f'{"" if ok else " disabled"}>{e(name)}'
            f'{"" if ok else " — " + e(why)}</option>'
            for name, ok, why in _for_slot(offered, slot_caps, fits))
        rows.append(
            f'<select name="v.{e(view_name)}.{e(region)}.{index}">{picks}</select>'
            + _placement_options(view_name, region, index, current, schemas,
                                 (options or {}).get((region, index)) or {}))
    return (f'<div class="slot-row"><span class="rg">{e(region)}'
            f'<span class="cap"><br>{w}&times;{h}</span></span>'
            f'<div>{"".join(rows)}</div></div>')


#: Progressive enhancement only. With this off the boxes are labels and the
#: selects still work -- which is the test for whether it belongs here at all.
#: Kept out of the f-string below because JavaScript is mostly braces.
_BUILDER_SCRIPT = """<script>
// The picture follows the form. Clicking a box focuses the select that fills
// it, and changing a select relabels the box -- so the arrangement can be read
// as a shape and edited as a list without either going stale.
document.querySelectorAll('.view').forEach(function (view) {
  var boxes = view.querySelectorAll('.mslot');
  boxes.forEach(function (box) {
    var sel = view.querySelector('select[name="' + box.dataset.for + '"]');
    if (!sel) { return; }
    var label = box.querySelector('.mn');
    function sync() {
      var value = sel.value || '';
      label.textContent = value || label.dataset.empty || label.textContent;
      box.classList.toggle('filled', !!value);
    }
    label.dataset.empty = label.textContent;
    box.addEventListener('click', function () {
      sel.focus();
      sel.scrollIntoView({block: 'center', behavior: 'smooth'});
    });
    sel.addEventListener('change', sync);
    sync();
  });
});
</script>"""


def _map(view_name: str, regions: dict, by_region: dict,
         panel_w: int, panel_h: int) -> str:
    """The panel, to scale, with a box for every slot.

    A list of dropdowns describes a layout; SPEC SS9's dashboard IS a shape,
    and choosing what goes where should look like the thing being chosen. The
    boxes are positioned as percentages of the real rectangles, so this is the
    same geometry the renderer uses rather than a drawing of it.

    It is a view of the form, not a second source of truth: every box names the
    select that fills it, and the script below keeps the two in step. With the
    script off, the boxes are labels and the selects still work.
    """
    if panel_w <= 0 or panel_h <= 0:
        return ""
    boxes = []
    for region, spec in regions.items():
        held = by_region.get(region) or []
        for index in range(spec["holds"]):
            x, y, w, h = layout.slots(spec, spec["holds"])[index]
            current = held[index] if index < len(held) else ""
            field = f"v.{e(view_name)}.{e(region)}.{index}"
            boxes.append(
                f'<div class="mslot" data-for="{field}" '
                f'style="left:{x / panel_w:.4%};top:{y / panel_h:.4%};'
                f'width:{w / panel_w:.4%};height:{h / panel_h:.4%}">'
                f'<span class="mn">{e(current) if current else VACANT}</span>'
                f'<span class="mr">{e(region)}</span></div>')
    return (f'<div class="map" style="aspect-ratio:{panel_w}/{panel_h}">'
            f'{"".join(boxes)}</div>')


def editor(hw: str, views: dict, regions: dict, offered, template: str,
           schemas=None, fits=None, caps=None) -> str:
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
                        offered, schemas, values, fits)
            for region, spec in regions.items())
        panel = _map(view_name, regions, by_region,
                     int((caps or {}).get("w") or 0),
                     int((caps or {}).get("h") or 0))
        blocks.append(f'<div class="view"><h3>{e(view_name)}</h3>'
                      f'{panel}{rows}</div>')

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
</div></div>
{_BUILDER_SCRIPT}"""


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
