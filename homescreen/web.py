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

def render_home(st: dict) -> str:
    e = html.escape

    def esc(v):
        return e(str(v)) if v is not None else "&mdash;"

    cards = []
    for d in st["devices"]:
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
        fleet_rows.append(f"""<div class="card">
  <div class="row"><span class="name">{name}</span>
    <span class="tag">{esc(d.get("hw"))}</span>
    <span class="tag">scene: {esc(d.get("scene") or "unassigned")}</span>
    <span class="tag">fw {esc(d.get("fw"))}</span>
    {'<span class="tag">' + e(dims) + '</span>' if dims else ''}
    <span class="tag">poll {esc(d.get("poll_seconds"))}s</span>
    {state}</div>
  <dl><dt>last seen</dt><dd>{esc(d.get("last_seen"))}</dd>
      <dt>first seen</dt><dd>{esc(d.get("first_seen"))}</dd>{tel_row}</dl>
</div>""")
    fleet_html = ("<h2>Fleet</h2>" + ("".join(fleet_rows)
                  or '<div class="card">no devices have called in yet</div>'))

    feed = st["feed"]
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

  {fleet_html}

  <h2>Devices (config)</h2>
  {"".join(cards) or '<div class="card">none registered</div>'}

  <footer>Machine-readable: <a href="/api/status">/api/status</a>.
  Config structure only &mdash; no secrets are rendered here.</footer>
</main>"""
