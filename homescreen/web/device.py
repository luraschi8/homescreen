"""One screen: what it is, what it shows, and how to change either."""

from __future__ import annotations

from . import schedule_ui, views_ui
from .fields import datalist_markup, option_group
from .layout import dash, e, page, pill, scene_label, when
from . import layout as _layout

_layout._with(schedule_ui.CSS)
_layout._with(views_ui.CSS)

#: Picking a component swaps its settings in without a round trip.
SCRIPT = """
document.querySelectorAll('[data-scene-picker]').forEach(function(sel){
  function sync(){
    document.querySelectorAll('.optgroup').forEach(function(g){
      var on = g.dataset.scene === sel.value;
      g.hidden = !on; g.disabled = !on;
    });
  }
  sel.addEventListener('change', sync); sync();
});
"""


def _telemetry(dev: dict) -> str:
    tel = dev.get("telemetry") or {}
    if not tel:
        return ""
    return ("<dt>telemetría</dt><dd>"
            + e(", ".join(f"{k}={v}" for k, v in sorted(tel.items())))
            + "</dd>")


def _notes(dev: dict) -> str:
    out = ""
    if dev.get("unsupported"):
        out += ('<dt>descartado</dt><dd style="color:var(--bad)">'
                + e(", ".join(str(c) for c in dev["unsupported"]))
                + " — esta pantalla no lo declara</dd>")
    if dev.get("scene_error"):
        out += ('<dt>escena</dt><dd style="color:var(--bad)">'
                + e(dev["scene_error"]) + "</dd>")
    return out


def _credentials(hw: str, creds) -> str:
    """Per-screen keys, if any component here needs one.

    The deployment's own key is on the settings page and every screen falls
    back to it. This is for the screen that needs a DIFFERENT one -- a second
    account, a personal calendar next to a work one -- which is a real thing to
    want and is why a job with its own key is its own fetch.
    """
    if not creds:
        return ""
    fields = []
    for state in creds:
        stored = state.get("set")
        fields.append(f"""<form class="stack" method="post"
      action="/device/{e(hw)}/secrets" style="margin-bottom:.9rem">
  <input type="hidden" name="provider" value="{e(state.get("provider"))}">
  <input type="hidden" name="secret" value="{e(state.get("name"))}">
  <input type="hidden" name="scope" value="{e(state.get("scope"))}">
  <label class="field">{e(state.get("provider"))} · {e(state.get("name"))}
    <input type="password" name="value" autocomplete="off"
      placeholder="{'\u2022' * 12 if stored else 'usa la del servidor'}">
    <span class="hint">{"Esta pantalla usa su propia clave."
                        if stored else
                        "En blanco usa la clave global de Ajustes."}</span>
  </label>
  <div class="actions"><button type="submit">Guardar</button>
    {'<button class="danger" type="submit" name="action" value="clear">'
     'Volver a la global</button>' if stored else ''}</div>
</form>""")
    return ('<h2>Credenciales de esta pantalla</h2>'
            f'<div class="panel"><div class="pad">{"".join(fields)}</div></div>')


def render_device(dev: dict, *, options: list, schemas: dict, name_max: int,
                  notice: str = "", plan=None, views=(), now: float = 0.0,
                  credentials=(), view_bodies=None, regions=None,
                  template: str = "") -> str:
    """`options` is [(scene, drawable, why)] for THIS device; `schemas` maps a
    scene to its option schema, so every component's settings travel with it."""
    hw = e(dev.get("hw"))
    approved = dev.get("approved", True)
    current = dev.get("scene") or "unassigned"
    caps = dev.get("caps") or {}

    state = (pill("esperando aprobación", "warn") if not approved
             else pill("en línea", "ok") if dev.get("online")
             else pill("sin conexión", "bad"))
    shape = (f'{caps["w"]}\u00d7{caps["h"]}' if "w" in caps and "h" in caps else None)

    known = {name for name, _, _ in options} | {"unassigned"}
    # A scene the server no longer recognises -- renamed or removed in an
    # upgrade -- left NOTHING selected, so the browser picked the first option
    # and pressing Guardar to rename the screen also reassigned it, silently.
    # An empty value already means "leave the scene alone" downstream.
    orphan = ("" if current in known else
              f'<option value="" selected>mantener: {e(current)}</option>')
    picks = orphan + "".join(
        f'<option value="{e(name)}"'
        f'{" selected" if not orphan and name == current else ""}'
        f'{"" if ok else " disabled"}>{e(name)}'
        f'{"" if ok else " — " + e(why)}</option>' for name, ok, why in options)
    picks += (f'<option value="unassigned"'
              f'{" selected" if not orphan and current == "unassigned" else ""}>'
              f'sin asignar</option>')

    groups = "".join(
        option_group(name, schemas.get(name) or [], dev.get("options"),
                     active=(name == current))
        for name, ok, _ in options if ok)
    lists = datalist_markup([f for s in schemas.values() for f in (s or [])])

    thumbs = "".join(
        f'<figure class="pv">'
        f'<img src="/api/devices/{hw}/preview.svg?view={e(name)}" '
        f'alt="{e(name)} en esta pantalla" loading="lazy">'
        f'<figcaption>{e(name)}</figcaption></figure>'
        for name, ok, _ in options if ok)

    unknown_scene = ("" if current in known else
                     f'<div class="notice">Esta pantalla tiene asignada '
                     f'<strong>{e(current)}</strong>, que este servidor ya no '
                     f'reconoce. Elige un componente para reemplazarla.</div>')

    admission = ("" if approved else f"""<div class="panel"><div class="pad">
  <p class="empty" style="margin-top:0">Esta pantalla ha llamado pero nadie la
  ha admitido todavía. Hasta entonces no recibe ninguna escena.</p>
  <form method="post" action="/device/{hw}/approval">
    <input type="hidden" name="approved" value="1">
    <button type="submit">Añadir a la flota</button></form>
</div></div>""")

    revoke = ("" if not approved else f"""
  <form method="post" action="/device/{hw}/approval">
    <input type="hidden" name="approved" value="0">
    <button class="ghost" type="submit">Sacar de la flota</button></form>""")

    body = f"""<p class="crumb"><a href="/">Flota</a> / {hw}</p>
<h1>{e(dev.get("name") or "sin nombre")}</h1>
<div class="pills" style="margin-bottom:1.2rem">{state}
  {pill("muestra: " + scene_label(dev.get("scene")))}
  {pill(shape) if shape else ""}
  {pill("cada " + str(dev.get("poll_seconds")) + "s")}
  {pill("fw " + str(dev.get("fw") or "?"))}</div>
{admission}{unknown_scene}

<h2>Qué muestra</h2>
<div class="panel"><div class="pad">
  <form class="stack" method="post" action="/device/{hw}">
    <label class="field">Nombre
      <input type="text" name="name" value="{e(dev.get("name") or "")}"
        maxlength="{name_max}" placeholder="sin nombre"></label>
    <label class="field">Componente
      <select name="scene" data-scene-picker>{picks}</select></label>
    {groups}
    <div class="actions"><button type="submit">Guardar</button></div>
  </form>
</div></div>
{views_ui.editor(dev.get("hw") or "", view_bodies or {}, regions or {},
                 options, template)}
{_credentials(dev.get("hw") or "", credentials)}
{schedule_ui.editor(dev.get("hw") or "", plan or {}, views, now) if views else ""}
{f'<h2>Vista previa</h2><div class="panel"><div class="pad"><div class="pvs">{thumbs}</div></div></div>' if thumbs else ""}

<h2>Detalles</h2>
<div class="panel"><div class="pad"><dl class="facts">
  <dt>id</dt><dd>{hw}</dd>
  <dt>primer contacto</dt><dd>{when(dev.get("first_seen"))}</dd>
  <dt>último contacto</dt><dd>{when(dev.get("last_seen"))}</dd>
  <dt>componentes</dt><dd>{e(", ".join(str(c) for c in (caps.get("components") or [])) or "—")}</dd>
  <dt>profundidad</dt><dd>{dash(caps.get("depth"))} bit</dd>
  {_telemetry(dev)}{_notes(dev)}
</dl></div></div>

<h2>Zona peligrosa</h2>
<div class="panel danger-zone"><div class="pad">
  <p class="empty" style="margin-top:0">Sacarla de la flota conserva su nombre y
  su escena. Eliminarla borra el registro; si la pantalla sigue encendida
  volverá a aparecer como solicitud.</p>
  <div class="actions">{revoke}
    <form method="post" action="/device/{hw}/remove"
          onsubmit="return confirm('¿Eliminar {hw} del registro? Se pierden su nombre y sus ajustes.')">
      <button class="danger" type="submit">Eliminar del registro</button></form>
  </div>
</div></div>
{lists}"""
    return page(f'{dev.get("name") or hw} — HomeScreen', body, active="fleet",
                notice=notice, script=SCRIPT)
