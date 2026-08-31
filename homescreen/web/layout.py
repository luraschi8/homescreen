"""The shell every dashboard page renders into: chrome, styling, small parts.

Split from the page modules so that changing how a table looks is not an edit
to what a table means. The CSS lives here as one string because the dashboard
is served from a Pi with no build step and no asset pipeline -- and because a
stylesheet that cannot be fetched is a dashboard that cannot be read.
"""

from __future__ import annotations

import html
from datetime import datetime

#: Kept local rather than pulled from a CDN. The dashboard's job is to tell you
#: what the fleet is doing, and the moment it needs the internet to render, it
#: stops working exactly when the network is the thing you are debugging.
CSS = """
:root{
  --bg:#f6f7f9; --panel:#fff; --fg:#14161a; --dim:#6b7280; --faint:#9aa1ab;
  --line:#e4e7ec; --line-soft:#eef1f5;
  --accent:#2563eb; --accent-fg:#fff;
  --ok:#0a7d33; --ok-bg:#e9f7ee; --bad:#b3261e; --bad-bg:#fdecea;
  --warn:#8a5a00; --warn-bg:#fff5e0;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --radius:10px;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#0f1113; --panel:#17191d; --fg:#e7e9ec; --dim:#9aa1ab; --faint:#6b7280;
  --line:#262a30; --line-soft:#1e2126;
  --accent:#3b82f6; --ok:#5ddb84; --ok-bg:#10281a; --bad:#ff6b5e;
  --bad-bg:#2a1512; --warn:#e0a94a; --warn-bg:#2a2113;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

header.top{border-bottom:1px solid var(--line);background:var(--panel)}
header.top .in{max-width:64rem;margin:0 auto;padding:.7rem 1.1rem;
  display:flex;align-items:center;gap:1.4rem;flex-wrap:wrap}
.brand{font-weight:650;letter-spacing:-.01em;color:var(--fg)}
.brand:hover{text-decoration:none}
nav.top a{color:var(--dim);font-size:.9rem;padding:.2rem 0;
  border-bottom:2px solid transparent}
nav.top a.on{color:var(--fg);border-bottom-color:var(--accent)}
nav.top{display:flex;gap:1.1rem}
.spacer{flex:1}
.meta{color:var(--faint);font-size:.78rem;font-family:var(--mono)}

main{max-width:64rem;margin:0 auto;padding:1.6rem 1.1rem 4rem}
h1{font-size:1.4rem;margin:0 0 .2rem;letter-spacing:-.015em}
h2{font-size:.74rem;text-transform:uppercase;letter-spacing:.08em;
  color:var(--dim);margin:2rem 0 .7rem;font-weight:650}
.lede{color:var(--dim);font-size:.88rem;margin:0 0 1.4rem}
.crumb{font-size:.82rem;color:var(--dim);margin:0 0 .8rem}

.panel{background:var(--panel);border:1px solid var(--line);
  border-radius:var(--radius);overflow:hidden}
/* Wide content scrolls INSIDE its panel. Without this the fleet table was
   clipped by the panel and the document did not scroll, so on a phone the
   state, last-contact and cadence columns were unreachable -- not cramped,
   unreachable. */
.scroll-x{overflow-x:auto;-webkit-overflow-scrolling:touch}
.panel+.panel{margin-top:.8rem}
.pad{padding:1rem 1.1rem}

table.fleet{width:100%;border-collapse:collapse;font-size:.9rem}
table.fleet th{text-align:left;font-size:.7rem;text-transform:uppercase;
  letter-spacing:.06em;color:var(--dim);font-weight:600;
  padding:.6rem 1.1rem;border-bottom:1px solid var(--line)}
table.fleet td{padding:.7rem 1.1rem;border-bottom:1px solid var(--line-soft);
  vertical-align:middle}
table.fleet tr:last-child td{border-bottom:0}
table.fleet tr:hover td{background:var(--line-soft)}
td.nm a{font-weight:600;color:var(--fg)}
td.nm .hw{display:block;font-family:var(--mono);font-size:.72rem;color:var(--faint)}
.unnamed{color:var(--faint);font-style:italic;font-weight:500}

.pill{display:inline-block;font-size:.72rem;padding:.1rem .5rem;border-radius:99px;
  border:1px solid var(--line);color:var(--dim);white-space:nowrap}
.pill.ok{color:var(--ok);background:var(--ok-bg);border-color:transparent}
.pill.bad{color:var(--bad);background:var(--bad-bg);border-color:transparent}
.pill.warn{color:var(--warn);background:var(--warn-bg);border-color:transparent}
.pills{display:flex;gap:.35rem;flex-wrap:wrap}

dl.facts{display:grid;grid-template-columns:max-content 1fr;gap:.45rem 1.1rem;
  margin:0;font-size:.86rem}
dl.facts dt{color:var(--dim)}
dl.facts dd{margin:0;font-family:var(--mono);word-break:break-word}

form.stack{display:flex;flex-direction:column;gap:.9rem}
label.field{display:flex;flex-direction:column;gap:.28rem;font-size:.82rem;
  color:var(--dim);max-width:32rem}
label.field .hint{font-size:.75rem;color:var(--faint);margin-top:.1rem}
form.stack>label.field,form.stack>label.check{margin-bottom:.15rem}
fieldset.optgroup .stack{gap:1rem}
input[type=text],input[type=number],input[type=url],select,textarea{
  font:inherit;font-size:.9rem;padding:.45rem .6rem;color:var(--fg);
  background:var(--bg);border:1px solid var(--line);border-radius:7px;width:100%}
input:focus,select:focus{outline:2px solid var(--accent);outline-offset:-1px}
label.check{display:flex;flex-direction:row;align-items:center;gap:.5rem;
  font-size:.86rem;color:var(--fg)}
label.check input{width:auto}

button{font:inherit;font-size:.88rem;padding:.45rem 1rem;border-radius:7px;
  border:1px solid transparent;background:var(--accent);color:var(--accent-fg);
  cursor:pointer;font-weight:550}
button:hover{filter:brightness(1.08)}
button.ghost{background:transparent;color:var(--fg);border-color:var(--line)}
button.ghost:hover{background:var(--line-soft);filter:none}
button.danger{background:transparent;color:var(--bad);border-color:var(--bad)}
button.danger:hover{background:var(--bad-bg);filter:none}
.actions{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}

.pvs{display:flex;gap:.8rem;flex-wrap:wrap}
figure.pv{margin:0;text-align:center}
figure.pv img{width:108px;height:108px;border-radius:9px;background:#000;
  display:block;border:1px solid var(--line)}
figure.pv figcaption{font-size:.74rem;color:var(--dim);margin-top:.3rem}

.notice{margin:0 0 1.1rem;padding:.65rem .85rem;border-radius:8px;
  background:var(--warn-bg);color:var(--warn);font-size:.86rem;
  border:1px solid transparent}
.notice.ok{background:var(--ok-bg);color:var(--ok)}
.notice.bad{background:var(--bad-bg);color:var(--bad)}
.empty{color:var(--dim);font-size:.88rem}
.danger-zone{border-color:var(--bad)}
.danger-zone h2{color:var(--bad)}
footer{max-width:64rem;margin:0 auto;padding:0 1.1rem 2.5rem;
  color:var(--faint);font-size:.78rem}
"""


def _with(extra: str) -> None:
    """Fold another module's styles into the one stylesheet the page ships.

    One <style> for the whole document, because the dashboard serves its own
    assets and a second request for a handful of rules is a request that can
    fail on the network you are using the page to debug.
    """
    global CSS
    if extra not in CSS:
        CSS += extra


def e(value) -> str:
    """Escape for HTML text and attributes. Everything here is operator- or
    device-supplied, and a device's name is whatever it POSTed."""
    return html.escape("" if value is None else str(value), quote=True)


#: What an absent value looks like. A literal character, NOT an entity: this
#: gets passed to pill(), which escapes, and "&mdash;" came out as "&amp;mdash;"
#: on screen. Anything that returns display text must be safe to escape twice.
EMPTY = "\u2014"


def dash(value) -> str:
    return e(value) if value not in (None, "") else EMPTY


def duration(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}d {(s % 86400) // 3600}h"


#: Scene names the server uses internally that are not components at all, and
#: that a human should not be shown in English next to Spanish chrome.
_SCENE_LABELS = {"unassigned": "sin asignar", "pending": "esperando aprobación",
                 "error": "error", "": "sin asignar", None: "sin asignar"}


def scene_label(scene) -> str:
    """What to call a screen's assignment on screen."""
    return _SCENE_LABELS.get(scene, str(scene))


def when(stamp, now: float | None = None) -> str:
    """An ISO stamp as something a person can read at a glance.

    The raw value is `2026-08-27T17:31:07.315886+02:00`. Six digits of
    microseconds in a column you scan to answer "is this thing alive?" is
    noise, and the date is noise too while the answer is "seconds ago".
    """
    if not stamp:
        return EMPTY
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return e(stamp)                  # unparseable: show it rather than lie
    reference = (datetime.now(moment.tzinfo) if now is None
                 else datetime.fromtimestamp(now, moment.tzinfo))
    delta = (reference - moment).total_seconds()
    if delta < 0:
        return e(moment.strftime("%H:%M:%S"))   # clock skew; do not say "in -3s"
    if delta < 90:
        return f"hace {int(delta)}s"
    if delta < 3600:
        return f"hace {int(delta // 60)} min"
    if reference.date() == moment.date():
        return e(moment.strftime("%H:%M"))
    return e(moment.strftime("%d/%m %H:%M"))


def pill(text: str, kind: str = "") -> str:
    return f'<span class="pill{" " + kind if kind else ""}">{e(text)}</span>'


def page(title: str, body: str, *, active: str = "", meta: str = "",
         notice: str = "", script: str = "") -> str:
    """The whole document. One place decides what a dashboard page looks like."""
    nav = "".join(
        f'<a href="{href}"{" class=\"on\"" if key == active else ""}>{e(label)}</a>'
        for key, href, label in (("fleet", "/", "Flota"),
                                 ("settings", "/settings", "Ajustes")))
    # Success and failure came through one `?m=` into one yellow bar, so every
    # save looked like a warning and every failure looked like a save. The
    # message says which it is; the bar should agree.
    kind = ""
    lowered = str(notice).lower()
    if any(w in lowered for w in ("no se pudo", "no existe", "error",
                                  "unavailable", "necesita", "no válid",
                                  "debe ", "vacío", "supera")):
        kind = " bad"
    elif notice:
        kind = " ok"
    notice_html = (f'<div class="notice{kind}">{e(notice)}</div>'
                   if notice else "")
    script_html = f"<script>{script}</script>" if script else ""
    return f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<style>{CSS}</style>
<header class="top"><div class="in">
  <a class="brand" href="/">HomeScreen</a>
  <nav class="top">{nav}</nav>
  <span class="spacer"></span>
  <span class="meta">{meta}</span>
</div></header>
<main>{notice_html}{body}</main>
<footer>API: <a href="/api/status">/api/status</a> &middot;
<a href="/api/devices">/api/devices</a></footer>
{script_html}
</html>"""
