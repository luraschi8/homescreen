"""The fleet page: who is here, what each one shows, and who wants in."""

from __future__ import annotations

from .layout import dash, duration, e, page, pill, scene_label


def _state(dev: dict) -> str:
    if not dev.get("approved", True):
        return pill("esperando aprobación", "warn")
    return pill("en línea", "ok") if dev.get("online") else pill("sin conexión", "bad")


def _name_cell(dev: dict) -> str:
    hw = e(dev.get("hw"))
    name = dev.get("name")
    shown = (f'<a href="/device/{hw}">{e(name)}</a>' if name
             else f'<a href="/device/{hw}" class="unnamed">sin nombre</a>')
    return f'<td class="nm">{shown}<span class="hw">{hw}</span></td>'


def _pending_card(dev: dict) -> str:
    hw = e(dev.get("hw"))
    caps = dev.get("caps") or {}
    shape = (f'{caps["w"]}\u00d7{caps["h"]}' if "w" in caps and "h" in caps
             else "geometría no declarada")
    comps = ", ".join(str(c) for c in (caps.get("components") or [])) or "—"
    return f"""<div class="panel"><div class="pad">
  <div class="pills" style="margin-bottom:.6rem">
    <strong style="font-family:var(--mono)">{hw}</strong>
    {pill(shape)}{pill(comps)}{pill("visto " + dash(dev.get("first_seen")))}
  </div>
  <div class="actions">
    <form method="post" action="/device/{hw}/approval">
      <input type="hidden" name="approved" value="1">
      <button type="submit">Añadir a la flota</button></form>
    <form method="post" action="/device/{hw}/remove">
      <button class="danger" type="submit">Descartar</button></form>
  </div>
</div></div>"""


def render_fleet(st: dict, notice: str = "") -> str:
    fleet = list(st.get("fleet") or [])
    waiting = [d for d in fleet if not d.get("approved", True)]
    members = [d for d in fleet if d.get("approved", True)]

    rows = "".join(f"""<tr>
  {_name_cell(d)}
  <td>{e(scene_label(d.get("scene")))}</td>
  <td>{_state(d)}</td>
  <td class="meta">{dash(d.get("last_seen"))}</td>
  <td class="meta">{e(d.get("poll_seconds"))}s</td>
</tr>""" for d in members)

    table = (f"""<div class="panel"><table class="fleet">
  <tr><th>Pantalla</th><th>Muestra</th><th>Estado</th>
      <th>Último contacto</th><th>Cadencia</th></tr>
  {rows}</table></div>"""
             if members else
             '<div class="panel"><div class="pad empty">'
             'Ninguna pantalla en la flota todavía. Enciende una y aparecerá '
             'abajo esperando aprobación.</div></div>')

    pending_html = ""
    if waiting:
        pending_html = ("<h2>Quieren unirse (%d)</h2>" % len(waiting)
                        + "".join(_pending_card(d) for d in waiting))

    feed = st.get("feed") or {}
    online = sum(1 for d in members if d.get("online"))
    body = f"""<h1>Flota</h1>
<p class="lede">{len(members)} pantalla(s) &middot; {online} en línea
  &middot; <a href="/settings">fuente de datos</a>:
  {e(feed.get("source"))} cada {e(feed.get("fetch_seconds"))}s</p>
{pending_html}
<h2>En la flota</h2>
{table}"""
    return page("HomeScreen — flota", body, active="fleet", notice=notice,
                meta=f'{e(st.get("version"))} &middot; '
                     f'{e(duration(st.get("uptime_s") or 0))}')
