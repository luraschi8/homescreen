"""Settings: the values the fetch daemon uses, and how the sources are doing.

These are per-PROVIDER, not per-screen. One fetch daemon serves every radar on
the LAN, so an endpoint set here is the endpoint they all read. When a
component needs its own parameters -- a ticker list, a city, a calendar URL --
those belong to the assignment and are edited on the screen's own page; this is
for the defaults underneath them.
"""

from __future__ import annotations

from .layout import dash, e, page


def _health(dev: dict) -> tuple[str, str]:
    """(state pill, detail rows) for one configured source."""
    feed = dev.get("feed") or {}
    detail = (f'<dt>aviones</dt><dd>{e(feed.get("aircraft"))}</dd>'
              f'<dt>antigüedad</dt><dd>{e(feed.get("age_s"))}s</dd>'
              f'<dt>última consulta</dt><dd>{dash(feed.get("fetched_at"))}</dd>')
    if feed.get("fetched_at") is None:
        return '<span class="pill bad">nunca consultado</span>', ""
    if feed.get("ok"):
        return '<span class="pill ok">al día</span>', detail
    # The error can be absent while ok is false -- an unparseable or future
    # `fetched_at` reaches here with error still null -- and a dangling em-dash
    # reads as a truncated message.
    why = feed.get("error")
    label = f"caducado — {e(why)}" if why else "caducado"
    return f'<span class="pill bad">{label}</span>', detail


def _sources(devices) -> str:
    """Per-source health, which used to be repeated under every screen.

    It belongs with the source: a stale feed is one fact about the Pi, and
    showing it under each panel made one problem look like several.
    """
    rows = []
    for dev in devices or ():
        if dev.get("feed") is None:
            # Shown, not skipped. Dropping it made a configured screen vanish
            # from the only page that lists sources, which reads as "not
            # configured" rather than "has no feed of its own".
            rows.append(
                '<div class="panel"><div class="pad">'
                '<div class="pills">'
                f'<strong>{e(dev.get("id"))}</strong>'
                f'<span class="pill">{e(dev.get("kind"))}</span>'
                '<span class="pill">sin fuente propia — se dibuja en el Pi</span>'
                '</div></div></div>')
            continue
        state, detail = _health(dev)
        # Where this source is actually served. The old page rendered these and
        # the split dropped them with nowhere to land -- so "how do I curl this
        # thing?" had no answer on any page.
        links = "".join(
            f'<dt>{e(k)}</dt><dd><a href="{e(v)}">{e(v)}</a></dd>'
            for k, v in (dev.get("endpoints") or {}).items() if v)
        rows.append(
            '<div class="panel"><div class="pad">'
            '<div class="pills" style="margin-bottom:.6rem">'
            f'<strong>{e(dev.get("id"))}</strong>'
            f'<span class="pill">{e(dev.get("kind"))}</span>'
            f'<span class="pill">{e(dev.get("render"))}</span>{state}</div>'
            f'<dl class="facts">{detail}{links}</dl></div></div>')
    if not rows:
        return ('<div class="panel"><div class="pad empty">'
                'Ninguna fuente configurada.</div></div>')
    return "".join(rows)


def render_settings(feed: dict, *, devices=None, editable: bool = True,
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
        '<div class="actions"><button type="submit">Guardar</button></div>'
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
            f'<h2>Estado de las fuentes</h2>{_sources(devices)}')
    return page("Ajustes — HomeScreen", body, active="settings", notice=notice,
                meta=e(version))
