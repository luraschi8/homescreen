"""HTML rendering for the human-facing pages.

Split out of serve.py, which had grown to ~490 lines across five concerns.
The fleet dashboard grows this file further; keeping markup out of the
routing module means neither has to be read to understand the other.
"""

from __future__ import annotations

import html


_HOME_CSS = """
:root{--bg:#fff;--fg:#111;--dim:#666;--line:#e3e3e3;--ok:#0a7d33;--bad:#b3261e;
      --card:#fafafa;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#151516;--fg:#e8e8e8;--dim:#9a9a9a;
      --line:#2c2c2e;--ok:#5ddb84;--bad:#ff6b5e;--card:#1d1d1f}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.25rem;background:var(--bg);color:var(--fg);
     font:15px/1.5 system-ui,-apple-system,sans-serif}
main{max-width:56rem;margin:0 auto}
h1{font-size:1.35rem;margin:0 0 .15rem;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:.85rem;margin-bottom:1.75rem}
.sub code{font-family:var(--mono)}
h2{font-size:.72rem;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);
   margin:2rem 0 .6rem;font-weight:600}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;
      padding:.9rem 1.1rem;margin-bottom:.7rem}
.row{display:flex;flex-wrap:wrap;gap:.5rem 1.5rem;align-items:baseline}
.name{font-weight:600}
.pvs{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}
.pv{margin:0;text-align:center}
.pv img{width:104px;height:104px;border-radius:8px;background:#000;
  display:block;border:1px solid #ddd}
.pv figcaption{font-size:11px;color:#666;margin-top:4px}
.cfg{display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;
  margin-top:12px;padding-top:12px;border-top:1px solid #e8e8e8}
.cfg label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:#666}
.cfg input,.cfg select{font:inherit;font-size:13px;padding:5px 7px;
  border:1px solid #ccc;border-radius:5px;background:#fff}
.cfg button{font:inherit;font-size:13px;padding:6px 14px;border:1px solid #333;
  border-radius:5px;background:#333;color:#fff;cursor:pointer}
.cfg button:hover{background:#000}
.notice{margin:0 0 14px;padding:10px 12px;border-radius:6px;
  background:#f0f6ff;border:1px solid #cfe0ff;font-size:13px}
.tag{font-size:.7rem;color:var(--dim);border:1px solid var(--line);
     border-radius:99px;padding:.05rem .5rem}
.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
dl{display:grid;grid-template-columns:auto 1fr;gap:.3rem 1rem;margin:.75rem 0 0;
   font-size:.85rem}
dt{color:var(--dim)}dd{margin:0;font-family:var(--mono);word-break:break-all}
a{color:inherit}
footer{margin-top:2.5rem;color:var(--dim);font-size:.78rem}
"""

def duration(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"

def render_home(st: dict, scene_options=None, name_max: int = 32,
                notice: str = "") -> str:
    e = html.escape

    def esc(v):
        return e(str(v)) if v is not None else "&mdash;"

    cards = []
    for d in st.get("devices") or []:
        f = d["feed"]
        if f is None:
            state = '<span class="tag">no feed &mdash; pixel push, Phase C</span>'
            detail = ""
        elif f["fetched_at"] is None:
            state = '<span class="bad">never fetched</span>'
            detail = ""
        else:
            state = (f'<span class="ok">healthy</span>' if f["ok"]
                     else f'<span class="bad">stale &mdash; {e(str(f["error"]))}</span>')
            detail = (f'<dt>aircraft</dt><dd>{f["aircraft"]}</dd>'
                      f'<dt>feed age</dt><dd>{f["age_s"]}s</dd>'
                      f'<dt>last fetch</dt><dd>{esc(f["fetched_at"])}</dd>')
        links = "".join(
            f'<dt>{k}</dt><dd><a href="{e(v)}">{e(v)}</a></dd>'
            for k, v in d["endpoints"].items() if v)
        tel = d["last_telemetry"]
        tel_row = ("<dt>last telemetry</dt><dd>"
                   + e(", ".join(f"{k}={v}" for k, v in tel.items())) + "</dd>"
                   ) if tel else ""
        cards.append(f"""<div class="card">
  <div class="row"><span class="name">{esc(d["id"])}</span>
    <span class="tag">{esc(d["kind"])}</span>
    <span class="tag">render: {esc(d["render"])}</span>
    <span class="tag">poll {esc(d["poll_seconds"])}s</span>
    {state}</div>
  <dl>{detail}{links}{tel_row}</dl>
</div>""")

    fleet_rows = []
    for d in st.get("fleet", []):
        state = ('<span class="ok">online</span>' if d.get("online")
                 else '<span class="bad">offline</span>')
        name = e(str(d["name"])) if d.get("name") else '<span class="tag">unnamed</span>'
        caps = d.get("caps") or {}
        dims = (f'{caps["w"]}x{caps["h"]}' if "w" in caps and "h" in caps else None)
        tel = d.get("telemetry") or {}
        tel_row = ("<dt>telemetry</dt><dd>"
                   + e(", ".join(f"{k}={v}" for k, v in sorted(tel.items())))
                   + "</dd>") if tel else ""
        # Spec §5.5 / §6.2. Without these two rows the operator sees a healthy
        # green card for a panel the server is quietly serving something else.
        notes = ""
        if d.get("unsupported"):
            notes += ('<dt>dropped</dt><dd class="bad">'
                      + e(", ".join(str(c) for c in d["unsupported"]))
                      + " &mdash; not declared by this device</dd>")
        if d.get("scene_error"):
            notes += ('<dt>scene</dt><dd class="bad">'
                      + e(str(d["scene_error"])) + "</dd>")
        # A plain HTML form, not fetch(): no JavaScript and no CDN, the same
        # constraint the scenes themselves live under. It keeps working from a
        # phone on a bad connection, and it degrades to a page reload rather
        # than to silence. The POST is backed by the same validation as the
        # JSON PATCH, so an unknown scene is refused by one code path.
        current = d.get("scene") or "unassigned"
        # Per device, not a global list: a scene this one cannot draw is shown
        # disabled with the reason, so the operator can see WHY rather than
        # picking it and getting "escena no soportada" on the glass.
        # One preview per drawable scene, so an operator picks by looking
        # rather than by reading a name and hoping.
        opts = (scene_options or {}).get(d.get("hw")) or []
        thumbs = "".join(
            f'<figure class="pv"><img src="/preview/{e(str(d.get("hw")))}/'
            f'{e(name)}.svg" alt="{e(name)} on this screen" loading="lazy">'
            f'<figcaption>{e(name)}</figcaption></figure>'
            for name, ok, _why in opts if ok)
        preview = f'<div class="pvs">{thumbs}</div>' if thumbs else ""
        options = "".join(
            f'<option value="{e(name)}"'
            f'{" selected" if name == current else ""}'
            f'{"" if ok else " disabled"}>'
            f'{e(name)}{"" if ok else " — " + e(why)}</option>'
            for name, ok, why in opts)
        options += (f'<option value="unassigned"'
                    f'{" selected" if current == "unassigned" else ""}>'
                    f'unassigned</option>')
        form = f"""<form class="cfg" method="post" action="/home/device">
    <input type="hidden" name="hw" value="{esc(d.get("hw"))}">
    <label>name <input name="name" value="{e(str(d.get("name") or ""))}"
      maxlength="{name_max}" placeholder="unnamed"></label>
    <label>shows <select name="scene">{options}</select></label>
    <button type="submit">apply</button>
  </form>"""
        fleet_rows.append(f"""<div class="card">
  <div class="row"><span class="name">{name}</span>
    <span class="tag">{esc(d.get("hw"))}</span>
    <span class="tag">scene: {esc(d.get("scene") or "unassigned")}</span>
    <span class="tag">fw {esc(d.get("fw"))}</span>
    {'<span class="tag">' + e(dims) + '</span>' if dims else ''}
    <span class="tag">poll {esc(d.get("poll_seconds"))}s</span>
    {state}</div>
  <dl><dt>last seen</dt><dd>{esc(d.get("last_seen"))}</dd>
      <dt>first seen</dt><dd>{esc(d.get("first_seen"))}</dd>{tel_row}{notes}</dl>
  {preview}
  {form}
</div>""")
    notice_html = (f'<div class="notice">{e(notice)}</div>' if notice else "")
    fleet_html = ("<h2>Fleet</h2>" + ("".join(fleet_rows)
                  or '<div class="card">no devices have called in yet</div>'))

    feed = st.get("feed") or {}
    return f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HomeScreen &mdash; {esc(st["version"])}</title>
<style>{_HOME_CSS}</style>
<main>
  <h1>HomeScreen display backend</h1>
  <div class="sub">version <code>{esc(st["version"])}</code>
    &middot; up {e(duration(st["uptime_s"]))}
    &middot; {len(st.get("fleet", []))} device(s) registered
    &middot; {sum(1 for d in st.get("fleet", []) if d.get("online"))} online</div>

  <h2>Upstream feed</h2>
  <div class="card"><dl>
    <dt>provider</dt><dd>{esc(feed["source"])}</dd>
    <dt>endpoint</dt><dd>{esc(feed["endpoint"])}</dd>
    <dt>fetch every</dt><dd>{esc(feed["fetch_seconds"])}s</dd>
  </dl></div>

  {notice_html}
  {fleet_html}

  <h2>Devices (config)</h2>
  {"".join(cards) or '<div class="card">none registered</div>'}

  <footer>Machine-readable: <a href="/api/status">/api/status</a>.
  Config structure only &mdash; no secrets are rendered here.</footer>
</main>"""
