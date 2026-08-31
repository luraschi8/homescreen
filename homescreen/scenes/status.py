"""Status scene: what a screen shows when it has nothing else to show.

Serves three jobs, all of them "tell a human what to do next":
  - an unassigned device names its own hardware id, so you can type it into
    the fleet view instead of guessing which board is which
  - a scene that failed says which one and why
  - a feed that is down says so, rather than showing a plausible blank
"""

from __future__ import annotations

import html
from datetime import datetime

from homescreen import draw
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import page

#: Anywhere. This scene is what an UNASSIGNED or FAILED screen shows, and
#: serve.py routes every such device here whatever its glass -- so declaring
#: itself rect-only was a lie that the router ignored, on the one scene that
#: has to work on hardware nobody has configured yet.
SURFACES = ({"min_short": 90},
            {"min_w": 90, "min_h": 40})    # the fallback must always have
                                           # somewhere it can be drawn

CSS = """
.wrap{padding:18px;display:flex;flex-direction:column;height:100%;
  align-items:center;justify-content:center;text-align:center}
.hw{font-size:20px;font-weight:500;margin:10px 0 4px;
  font-family:'DejaVu Sans Mono',monospace}
.msg{margin-top:10px;max-width:80%}
"""


def build(ctx: SceneContext, *, message: str | None = None) -> Scene:
    w = int(ctx.caps.get("w") or 800)
    h = int(ctx.caps.get("h") or 480)
    # Escaped: a device name reaches this page, and `_check_name` only blocks
    # `/`. A name of `<style>*{display:none}` renders a blank panel; one of
    # `<div style="position:fixed;inset:0;background:#000">` renders a full
    # black refresh, which is the worst case for e-paper ghosting.
    e = html.escape
    hw = e(str(ctx.device.get("hw") or ctx.device.get("id") or "unknown"))
    name = ctx.device.get("name")
    name = e(str(name)) if name else None
    stamp = datetime.fromtimestamp(ctx.now).strftime("%H:%M")
    text = e(str(message)) if message else "sin escena asignada"
    raw_hw = str(ctx.device.get("hw") or ctx.device.get("id") or "unknown")
    raw_name = ctx.device.get("name")
    body = (f'<div class="wrap">'
            f'<div class="lab">{"sin asignar" if not name else name}</div>'
            f'<div class="hw">{hw}</div>'
            f'<div class="sec msg">{text}</div>'
            f'<div class="ter" style="margin-top:14px">{stamp}</div>'
            f'</div>')
    # Host stats: nothing here is worth waking a panel for more often.
    # A DRAW LIST too, and this is the important half. A data-push panel
    # plugged in for the first time renders this scene -- and with no
    # instructions it drew nothing at all, so the one screen whose entire job
    # is to tell you its hardware id could not tell you anything. It was the
    # first thing anybody saw and it was blank.
    headline = str(message or "")
    instructions = [
        draw.text("rim_top", "SIN ASIGNAR" if not headline else "AVISO", "xs",
                  "warn"),
        draw.text("center", raw_hw, "md"),
        draw.text("below", str(raw_name) if raw_name else "sin nombre", "sm",
                  "dim"),
        draw.text("rim_bottom", headline[:28] if headline else stamp, "xs",
                  "dim"),
    ]
    return Scene(layout="fill", poll_s=30,
                 components=({"c": "status", "draw": instructions},),
                 html=page(w, h, body, CSS))
