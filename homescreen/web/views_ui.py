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
from homescreen.web.layout import e, scene_label

CSS = """
/* The card border already says "these belong together"; the left rule said it
   a second time, and only around HALF the group -- Titulo and Tamano sat
   outside the indent while the component's own options sat inside it. */
.p-opts{margin:var(--s3) 0 0}
.p-opts label.field{max-width:100%}
.view{border:1px solid var(--line);border-radius:var(--radius);
  padding:var(--s4);margin-bottom:var(--s3)}
.view h3{margin:0 0 var(--s3);font-size:var(--fs-base);font-weight:600}
/* Each placement is a CARD. The gap between two different components used to
   equal the gap between two fields of the same one, so eleven placements read
   as a single four-thousand-pixel list with nothing marking where a block
   began. */
.slot{border:1px solid var(--line);border-radius:var(--radius);
  padding:var(--s3) var(--s4);margin-bottom:var(--s3);background:var(--panel)}
.slot > select{font-weight:600}
/* An empty slot is one line, not a full card of controls for a block that
   does not exist. */
.slot.vacant{background:transparent;border-style:dashed}
.slot.vacant .slot-extra,.slot.vacant .p-opts{display:none}
/* Side by side, and each with its own label. */
.slot-extra{display:flex;gap:var(--s3);margin:var(--s2) 0 0;flex-wrap:wrap}
.slot-extra .field{flex:1 1 12rem;margin:0}
/* No fixed 11rem inside a column that can be 150px wide: that, with the
   9rem gutter below, is why the page overflowed a phone by 55px and clipped
   every paragraph on it. */
.slot-extra .field:last-child{flex:1 1 10rem}
/* A heading above its slots, not a note in a gutter. `align-items:center`
   against a group two thousand pixels tall put the label for `main_left` six
   hundred pixels below the first field it named -- and the 9rem gutter never
   collapsed, which is half of why a phone had to scroll sideways. */
.slot-row{display:block;margin-bottom:var(--s6)}
.slot-row .rg{display:block;font-size:var(--fs-sm);font-weight:600;
  color:var(--fg);margin:0 0 var(--s2)}
.slot-row .cap{font-family:var(--mono);font-size:var(--fs-xs);
  color:var(--faint);font-weight:400}

/* The panel, to scale. Boxes are positioned as percentages of the real
   rectangles, so the picture cannot drift from the geometry it describes. */
.map{position:relative;width:100%;max-width:26rem;margin:0 0 var(--s4);
  background:var(--bg);border:1px solid var(--line);
  border-radius:var(--radius);overflow:hidden}
/* A round panel is round. The 1-bit rules do not apply in a browser. */
.map.round{border-radius:50%;max-width:13rem}
.mslot{position:absolute;display:flex;flex-direction:column;
  align-items:center;justify-content:center;gap:.1rem;overflow:hidden;
  box-sizing:border-box;border:1px dashed var(--line);border-radius:3px;
  background:var(--panel);cursor:pointer;padding:2px;text-align:center;
  transition:background .12s,border-color .12s}
.mslot:hover{border-color:var(--accent)}
.mslot.filled{border-style:solid;border-color:var(--accent)}
.mslot .mn{font-size:.7rem;font-weight:650;line-height:1.15;
  color:var(--faint);overflow:hidden;text-overflow:ellipsis;max-width:100%;
  white-space:nowrap}
.mslot.filled .mn{color:var(--fg)}
/* The region name is context, not content: it shows when there is room. */
/* An unfilled slot overlaid on the region's trailing edge: a target for
   adding the next block, not a picture of space the panel will have. */
.mslot.free{border-style:dotted;background:transparent;opacity:.75;
  z-index:2}
.mslot.free .mn{font-size:.6rem}
.mslot .mr{font-size:.58rem;color:var(--faint);font-family:var(--mono);
  line-height:1;overflow:hidden;max-width:100%}
@media (max-width:640px){.mslot .mr{display:none}}
"""

#: How tall an unfilled slot is drawn in the map. Enough to be a click target
#: and to fit the word "vacío"; the filled blocks give up the room.
FREE_SLOT_PX = 18

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
            f'name="o.{e(view_name)}.{e(region)}.{index}.{key}"'))
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


#: What a region is called to a person. The template's own keys are English
#: identifiers -- `main_left`, `masthead` -- and they were printed raw, in
#: monospace, in a gutter: the only clue to which slot you were editing, in a
#: language the rest of the page is not written in.
REGION_LABELS = {
    "masthead": "cabecera",
    "main_left": "columna izquierda",
    "main_right": "columna derecha",
    "markets": "banda inferior",
    "full": "toda la pantalla",
    "top": "mitad de arriba",
    "bottom": "mitad de abajo",
}


def region_label(name: str) -> str:
    return REGION_LABELS.get(str(name), str(name))


def _region_row(view_name: str, region: str, spec: dict, chosen: list,
                offered, schemas=None, options=None, fits=None,
                extras=None) -> str:
    """One region, and what is in it.

    A region that holds several gets several selects, because "add another" and
    "remove" are the same control when an empty slot means empty: choosing the
    dash removes it, and the row below is where the next one goes.
    """
    x, y, w, h = spec["rect"]
    shares = [((extras or {}).get((region, i)) or {}).get("weight")
              for i in range(spec["holds"])]
    rows = []
    for index in range(spec["holds"]):
        current = chosen[index] if index < len(chosen) else ""
        # Judged at the size this slot ACTUALLY gets: divided the way the
        # compositor divides it, by the shares the view asked for. Measuring
        # an even share instead offered a block given `weight: 5` the picker
        # for a fifth of the column and then drew it five times that -- so
        # components were refused for not fitting a rectangle they would
        # never be drawn in.
        filled = max(len(chosen), index + 1)
        here = layout.slots(spec, filled, shares)[index]
        slot_caps = {"w": here[2], "h": here[3]}
        picks = f'<option value="">{EMPTY}</option>' + "".join(
            f'<option value="{e(name)}"{" selected" if name == current else ""}'
            f'{"" if ok else " disabled"}>{e(scene_label(name))}'
            f'{"" if ok else " — " + e(why)}</option>'
            for name, ok, why in _for_slot(offered, slot_caps, fits))
        held = (extras or {}).get((region, index)) or {}
        # Heading and share sit WITH the select, because all three describe the
        # slot rather than the component in it: moving a block moves them.
        #
        # Rendered for every region, including one that holds a single block.
        # They used to appear only when `holds > 1` while `parse` read them
        # unconditionally, so a masthead heading was erased by re-posting the
        # form with no edits at all.
        # Both LABELLED. The share was a bare number box whose only
        # explanation was a `title` tooltip, sitting under an equally bare
        # title box -- so the one control that decides how the column is
        # divided looked like an unexplained "1" or "2,1", and a reviewer read
        # it as a row/column coordinate.
        trim = (f'<div class="slot-extra">'
                f'<label class="field"><span>Título</span>'
                f'<input type="text" name="l.{e(view_name)}.{e(region)}.{index}"'
                f' maxlength="24" placeholder="opcional"'
                f' value="{e(held.get("label") or "")}"></label>'
                f'<label class="field"><span>Tamaño</span>'
                f'<input type="number" name="wt.{e(view_name)}.{e(region)}.'
                # `step="any"`, because the browser refuses to submit a form whose
                # number is not a multiple of `step` -- and a weight of 1.4,
                # perfectly valid and set through the API, made the whole
                # arrangement unsubmittable with the complaint pointing at a
                # field thousands of pixels away. The range is enforced where it
                # can explain itself: `layout._weights`.
                f'{index}" min="0.25" max="20" step="any" placeholder="1"'
                f' value="{e(held.get("weight") or "")}">'
                f'<span class="hint">Cuánto ocupa frente a sus vecinos: 2 es '
                f'el doble que 1. En blanco, a partes iguales.</span>'
                f'</label></div>')
        # A card per placement, and a labelled select: fifteen bare dropdowns
        # with the region named once in a gutter left "which slot am I in?"
        # answerable only by counting.
        seat = f"{region_label(region)} · bloque {index + 1}" \
            if spec["holds"] > 1 else region_label(region)
        field = f"v.{e(view_name)}.{e(region)}.{index}"
        rows.append(
            f'<div class="slot{"" if current else " vacant"}">'
            f'<label class="field" for="{field}"><span>{e(seat)}</span>'
            f'<select name="{field}" id="{field}">{picks}</select></label>'
            + trim
            + _placement_options(view_name, region, index, current, schemas,
                                 (options or {}).get((region, index)) or {})
            + '</div>')
    return (f'<div class="slot-row"><span class="rg">{e(region_label(region))} '
            f'<span class="cap">{w}&times;{h}</span></span>'
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


def _template_picker(choices, current: str) -> str:
    """How the panel is divided up. Nothing else here means anything until it
    is chosen, so it sits above the picture rather than below the blocks."""
    if len(choices) < 2:
        return ""
    options = "".join(
        f'<option value="{e(name)}"{" selected" if name == current else ""}>'
        f'{e(label)}</option>' for name, label in choices)
    return (f'<label class="field">Distribución'
            f'<select name="template">{options}</select>'
            f'<span class="hint">Cambia cómo se divide la pantalla. Los '
            f'bloques que no quepan en la nueva distribución se quitan.</span>'
            f'</label>')


def _map(view_name: str, regions: dict, by_region: dict, weights: dict,
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
        # Cut by what is FILLED, exactly as `compose` cuts it. Dividing by
        # capacity instead drew three blocks in a five-slot column at 55/68/34
        # against the panel's 116/146/73, and showed empty space that will not
        # exist -- a picture that systematically understates every block.
        asked = list(weights.get(region) or [])
        filled = len(held)
        if not filled:
            # Nothing in it yet, so there is no reality to be faithful to.
            # Show the template's own shape instead -- it is what the region
            # WOULD look like, and for the markets band that is the whole
            # point: the wide FX cell is visible before anything is in it.
            cut = layout.slots(spec, spec["holds"])
            filled = spec["holds"]
        else:
            cut = layout.slots(spec, filled, asked)
        # Two requirements that pull against each other. The filled blocks
        # must be drawn at the size the PANEL will draw them -- dividing by
        # capacity instead showed every block too small and empty space that
        # would not exist. And the free slots must stay CLICKABLE, because the
        # script makes them the click-to-focus targets, and taking their
        # height from what `slots()` left over gave them a single device pixel.
        #
        # So the filled blocks keep their exact rectangles and the free slots
        # are OVERLAID along the region's trailing edge as a badge strip. The
        # picture stays honest about the arrangement, and adding the next
        # block stays a thing you can hit.
        rx, ry, rw, rh = spec["rect"]
        horizontal = spec.get("stack") == "h"
        spare = spec["holds"] - filled
        strip = min(FREE_SLOT_PX, (rw if horizontal else rh) // 2)

        for index in range(spec["holds"]):
            if index < filled:
                x, y, w, h = cut[index]
            elif horizontal:
                each = max(1, (rw - strip) // max(1, spare))
                x, y = rx + strip + (index - filled) * each, ry + rh - strip
                w, h = each, strip
            else:
                each = max(1, rw // max(1, spare))
                x, y = rx + (index - filled) * each, ry + rh - strip
                w, h = each, strip
            current = held[index] if index < len(held) else ""
            field = f"v.{e(view_name)}.{e(region)}.{index}"
            # The region names itself ONCE. Repeating it in all six cells of
            # the markets band labels the container six times and the contents
            # never, which is the opposite of what the picture is for.
            caption = (f'<span class="mr">{e(region)}</span>' if index == 0
                       else "")
            boxes.append(
                f'<div class="mslot{"" if index < filled else " free"}" '
                f'data-for="{field}" '
                f'style="left:{x / panel_w:.4%};top:{y / panel_h:.4%};'
                f'width:{w / panel_w:.4%};height:{h / panel_h:.4%}">'
                f'<span class="mn">{e(scene_label(current)) if current else VACANT}</span>'
                f'{caption}</div>')
    return (f'<div class="map" style="aspect-ratio:{panel_w}/{panel_h}">'
            f'{"".join(boxes)}</div>')


def editor(hw: str, views: dict, regions: dict, offered, template: str,
           schemas=None, fits=None, caps=None, templates=()) -> str:
    """Every view on this screen, and what each holds.

    Options are NOT edited here. A placement's settings belong to the component
    and are already edited above; putting a second copy beside the arrangement
    would be two forms writing one value, which is how they come to disagree.
    """
    # Rendered when there is a CHOICE, not only when a composed arrangement is
    # already in force. A device registers on `single`, so it has one region,
    # so this returned "" -- and this was the only place that could have
    # offered a different arrangement. The composed panel was unreachable from
    # the web UI entirely; the only way in was to PUT JSON at the API.
    choices = tuple(templates or ())
    # Also when the screen HAS more than one view, whatever its geometry. The
    # round panel has one region and one template, so this returned "" -- while
    # that same panel carries `tiempo` and `noche` and a 23:00-07:00 schedule
    # switching between them. The schedule editor offered views this page could
    # not create, rename or delete, and options are deliberately not edited
    # here, so there is nothing for it to duplicate.
    if len(regions) < 2 and len(choices) < 2 and len(views or {}) < 2:
        return ""
    blocks = []
    for view_name in sorted(views):
        placements = (views[view_name] or {}).get("placements") or []
        by_region: dict = {}
        values: dict = {}
        extras: dict = {}
        weights: dict = {}
        for placement in placements:
            region = placement.get("region")
            slot = len(by_region.setdefault(region, []))
            by_region[region].append(placement.get("component"))
            values[(region, slot)] = placement.get("options") or {}
            extras[(region, slot)] = {
                "label": placement.get("label") or "",
                "weight": placement.get("weight") or ""}
            weights.setdefault(region, []).append(placement.get("weight"))
        rows = "".join(
            _region_row(view_name, region, spec, by_region.get(region, []),
                        offered, schemas, values, fits, extras)
            for region, spec in regions.items())
        panel = _map(view_name, regions, by_region, weights,
                     int((caps or {}).get("w") or 0),
                     int((caps or {}).get("h") or 0))
        blocks.append(f'<div class="view"><h3>{e(view_name)}</h3>'
                      f'{panel}{rows}</div>')

    return f"""<h2>Qué contiene cada vista</h2>
<div class="panel"><div class="pad">
  <p class="empty" style="margin-top:0">Distribución <strong>{e(dict(choices).get(template, template))}</strong>.
  Cada región muestra su tamaño real en píxeles. Deja «{EMPTY}» para vaciarla.
  Cada hueco tiene sus propios ajustes: dos calendarios en una pantalla son dos
  calendarios distintos.</p>
  <form class="stack" method="post" action="/device/{e(hw)}/views">
    {_template_picker(choices, template)}
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
                    # Blank means "no opinion", which `clean_placement` reads
                    # as an even share -- not as zero.
                    "label": (form.get(f"l.{view_name}.{region}.{index}")
                              or "").strip()[:24],
                    "weight": (form.get(f"wt.{view_name}.{region}.{index}")
                               or "").strip() or None,
                    # None when this slot posted no fields at all, so a
                    # component with no options keeps whatever it had rather
                    # than being reset to empty.
                    "options": raw or None})
        views[view_name] = {"placements": placements}
    return views
