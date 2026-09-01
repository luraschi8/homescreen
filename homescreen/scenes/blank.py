"""Blank scene: a screen deliberately showing nothing.

There was no way to say "off at night". A screen with nothing assigned shows
the status card -- correctly, because an unassigned panel has to explain
itself -- so silence and misconfiguration looked identical, and the only way
to darken a bedroom panel was to unplug it.

This is the thing a schedule switches TO. The schedule already exists and is
per screen, so `22:00 -> apagado, 07:00 -> reloj` needs nothing new beyond a
view worth switching to.

It is `blank`, not `off`. Painting the round panel black does not cut its
backlight -- there is no backlight pin in the wiring, and SPEC's BOM has no
transistor for one -- so in a dark room it still glows faintly. Calling it
"off" would promise hardware we do not have.
"""

from __future__ import annotations

from homescreen import draw
from homescreen.scenes import Scene, SceneContext
from homescreen.scenes._style import page

#: Every screen, at any size. A blank panel has no legibility floor: there is
#: nothing to read, which is the point. This is the one component that can be
#: put in the narrowest cell of a markets band without lying about it.
SURFACES = ({"variant": "panel", "at": (127, 62), "min_w": 1, "min_h": 1},)

OPTIONS = (
    {"key": "tone", "label": "Color", "type": "choice",
     "choices": [t for t in draw.TONES],
     "default": "off",
     "hint": "«off» es el fondo del panel: negro en la pantalla redonda."},
)

CSS = """
.blank{width:100%;height:100%}
"""


def build(ctx: SceneContext) -> Scene:
    tone = str((ctx.options or {}).get("tone") or "off")
    if tone not in draw.TONES:
        tone = "off"

    # A 1-bit panel is the exception, and physics decides it: e-paper holds an
    # image with no power, so a black page costs nothing to keep but takes ~3s
    # of full refresh to reach and leaves the worst ghosting. Blank there means
    # white -- no ink, the state the panel is happiest resting in.
    w = int(ctx.caps.get("w") or 240)
    h = int(ctx.caps.get("h") or 240)
    depth = int(ctx.caps.get("depth") or 16)
    ink = "#000" if depth > 1 else "#fff"
    # Through `page()` like every other scene, so the root box is the exact
    # pixel size of the region. Sizing it in percentages let it escape: the
    # composer appends a fragment's scoped CSS after its positioning rule at
    # the same specificity, so `width:100%` won and painted the whole panel.
    html = page(w, h, '<div class="blank"></div>',
                f".blank{{width:100%;height:100%;background:{ink}}}")

    return Scene(
        layout="fill",
        components=({"c": "blank", "draw": [draw.fill(tone)]},),
        html=html,
        # Nothing changes. Ask again in an hour -- enough that a schedule
        # boundary is never more than an hour late, cheap enough that a
        # darkened panel is not the busiest thing on the network.
        poll_s=3600,
        poll_max_s=7200,
    )
