"""Settings: the values the fetch daemon uses, and how the sources are doing.

These are per-PROVIDER, not per-screen. One fetch daemon serves every radar on
the LAN, so an endpoint set here is the endpoint they all read. When a
component needs its own parameters -- a ticker list, a city, a calendar URL --
those belong to the assignment and are edited on the screen's own page; this is
for the defaults underneath them.
"""

from __future__ import annotations

from .layout import dash, e, page, when


def _job_row(job: dict) -> str:
    """One fetch, and how it is doing."""
    if job.get("fetched_at") is None:
        state = '<span class="pill bad">nunca consultado</span>'
    elif job.get("ok"):
        state = '<span class="pill ok">al día</span>'
    else:
        why = job.get("error")
        state = ('<span class="pill bad">'
                 + (f"fallando — {e(why)}" if why else "fallando")
                 + "</span>")
    params = ", ".join(f"{e(k)}={e(v)}" for k, v in
                       sorted((job.get("params") or {}).items())
                       # An endpoint is long and identical across jobs; it is
                       # on this page already, above.
                       if k != "endpoint")
    wanted = ", ".join(str(w) for w in (job.get("wanted_by") or ()))
    return (
        '<div class="panel"><div class="pad">'
        '<div class="pills" style="margin-bottom:.6rem">'
        f'<strong>{e(job.get("provider"))}</strong>'
        f'<span class="pill">cada {e(job.get("interval_s"))}s</span>{state}</div>'
        '<dl class="facts">'
        f'<dt>parámetros</dt><dd>{params or "—"}</dd>'
        f'<dt>última consulta</dt><dd>{when(job.get("fetched_at"))}</dd>'
        f'<dt>lo pide</dt><dd>{e(wanted) or "—"}</dd>'
        '</dl></div></div>')


def _sources(job_list) -> str:
    """Every fetch the fleet currently implies.

    Derived from assignments, so this cannot list a job nobody wants or omit
    one somebody does -- and it is the same derivation the daemon runs, not a
    second opinion about it.
    """
    rows = [_job_row(j) for j in job_list or ()]
    if not rows:
        return ('<div class="panel"><div class="pad empty">'
                'Ninguna pantalla pide datos todavía. Las descargas aparecen '
                'aquí en cuanto una escena las necesita.</div></div>')
    return "".join(rows)


def _secret_field(provider: str, state: dict) -> str:
    """One credential: settable, never shown.

    The input is empty even when a key is stored, because there is nothing to
    put in it -- no route returns a value. What it shows instead is that one is
    set and when, which is what someone debugging "why is weather failing"
    actually needs.
    """
    name = state.get("name")
    stored = state.get("set")
    since = state.get("updated_at")
    hint = (f"Guardada el {e(when(since))}. Escribe otra para reemplazarla."
            if stored else "No hay ninguna guardada.")
    return f"""<form class="stack" method="post" action="/settings/secrets"
      style="margin-bottom:1rem">
  <input type="hidden" name="provider" value="{e(provider)}">
  <input type="hidden" name="secret" value="{e(name)}">
  <label class="field">{e(provider)} · {e(name)}
    <input type="password" name="value" autocomplete="off"
      placeholder="{'\u2022' * 12 if stored else 'sin configurar'}">
    <span class="hint">{hint} Nunca se muestra.</span></label>
  <div class="actions">
    <button type="submit">Guardar esta clave</button>
    {'<button class="danger" type="submit" name="action" value="clear">Borrar</button>'
     if stored else ''}
  </div>
</form>"""


def _credentials(provider_list) -> str:
    forms = []
    for provider in provider_list or ():
        for state in provider.get("secrets") or ():
            forms.append(_secret_field(provider.get("name"), state))
    if not forms:
        return ('<div class="panel"><div class="pad empty">'
                'Ninguna fuente necesita credenciales todavía.</div></div>')
    return f'<div class="panel"><div class="pad">{"".join(forms)}</div></div>'


def render_settings(feed: dict, *, jobs=None, providers=None,
                    editable: bool = True,
                    notice: str = "", version: str = "") -> str:
    feed = feed or {}
    form = (
        '<form class="stack" method="post" action="/settings">'
        '<label class="field">Endpoint'
        f'<input type="text" name="endpoint" value="{e(feed.get("endpoint") or "")}"'
        ' placeholder="https://…">'
        '<span class="hint">De dónde saca el demonio los aviones. Lo leen todas'
        ' las pantallas con radar.</span></label>'
        '<label class="field">Consultar cada'
        f'<input type="number" name="fetch_seconds" value="{e(feed.get("fetch_seconds"))}"'
        ' min="1" max="3600">'
        '<span class="hint">Segundos entre consultas al endpoint. Es distinto de'
        ' cada cuánto una pantalla pregunta al Pi: eso lo decide su'
        ' componente.</span></label>'
        '<div class="actions"><button type="submit">Guardar la fuente</button></div>'
        '</form>')
    read_only = (
        '<dl class="facts">'
        f'<dt>endpoint</dt><dd>{dash(feed.get("endpoint"))}</dd>'
        f'<dt>cada</dt><dd>{dash(feed.get("fetch_seconds"))}s</dd></dl>'
        '<p class="empty">Definido en <code>config.yaml</code> en el Pi.</p>')

    body = (f'<h1>Ajustes</h1>'
            '<p class="lede">Valores de las fuentes de datos, compartidos por'
            ' toda la flota.</p>'
            f'<h2>Fuente ADS-B ({e(feed.get("source"))})</h2>'
            f'<div class="panel"><div class="pad">'
            f'{form if editable else read_only}</div></div>'
            f'<h2>Credenciales</h2>{_credentials(providers)}'
            f'<h2>Descargas en curso</h2>{_sources(jobs)}')
    return page("Ajustes — HomeScreen", body, active="settings", notice=notice,
                meta=e(version))
