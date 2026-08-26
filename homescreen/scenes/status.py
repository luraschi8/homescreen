"""Status scene: what a screen shows when it has nothing else to show.

Serves three jobs, all of them "tell a human what to do next":
  - an unassigned device names its own hardware id, so you can type it into
    the fleet view instead of guessing which board is which
  - a scene that failed says which one and why
  - a feed that is down says so, rather than showing a plausible blank
"""

from __future__ import annotations

from datetime import datetime

from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import page

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
    hw = ctx.device.get("hw") or ctx.device.get("id") or "unknown"
    name = ctx.device.get("name")
    stamp = datetime.fromtimestamp(ctx.now).strftime("%H:%M")
    text = message or "no scene assigned"
    body = (f'<div class="wrap">'
            f'<div class="lab">{"sin asignar" if not name else name}</div>'
            f'<div class="hw">{hw}</div>'
            f'<div class="sec msg">{text}</div>'
            f'<div class="ter" style="margin-top:14px">{stamp}</div>'
            f'</div>')
    return Scene(layout="fill", html=page(w, h, body, CSS))
