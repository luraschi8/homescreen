"""Several components on one page.

A pixel-push panel takes a rendered framebuffer, so a composed dashboard is one
HTML document with each placement drawn into its region's rectangle. Every
component already produces a whole page for the geometry it is given; composing
is placing those pages side by side and keeping their styles apart.

The interesting problem is isolation. Components write ordinary selectors --
`clock.py` styles `.big`, and so would a stocks component -- because each was
written believing it owned the document. Rather than make every component adopt
a naming convention it cannot verify, the composer scopes each fragment's CSS
at compose time: one wrapper id per placement, and every selector in that
fragment prefixed with it. Components change nothing and cannot collide.
"""

from __future__ import annotations

import re

from homescreen import layout, scenes
from homescreen.scenes._style import BASE_CSS

#: Pulled apart rather than parsed: this is the shape `page()` emits, and it is
#: the only shape that reaches here.
_STYLE = re.compile(r"<style>(?P<css>.*?)</style>(?P<body>.*)\Z", re.S)

#: Selectors that mean "the document" and must become the placement's own box
#: rather than the page's -- otherwise the first fragment's `body{...}` sets
#: the size of the whole composition.
_ROOT_SELECTORS = ("html,body", "html", "body", ":root", "*")


def scope_css(css: str, wrapper: str) -> str:
    """Prefix every selector so a fragment's styles cannot leave its region.

    A small, testable string transform rather than a CSS parser: the input is
    what our own `page()` produced, and the alternative -- asking every
    component to prefix its own classes -- is a convention that fails silently
    the first time somebody forgets.
    """
    out = []
    for block in _split_blocks(css):
        selector, _, rest = block.partition("{")
        if not rest:
            continue
        selector = selector.strip()
        if selector.startswith("@"):
            # A media or font rule: keep the wrapper, scope what is inside it.
            inner = rest.rsplit("}", 1)[0]
            out.append(f"{selector}{{{scope_css(inner, wrapper)}}}")
            continue
        scoped = ", ".join(
            f"#{wrapper}" if part.strip() in _ROOT_SELECTORS
            else f"#{wrapper} {part.strip()}"
            for part in selector.split(",") if part.strip())
        out.append(f"{scoped}{{{rest}")
    return "".join(out)


def _split_blocks(css: str):
    """CSS blocks, honouring one level of nesting (`@media`)."""
    depth, start = 0, 0
    for i, char in enumerate(css):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                yield css[start:i + 1]
                start = i + 1
    tail = css[start:].strip()
    if tail:
        yield tail


def fragment(html: str, wrapper: str) -> tuple:
    """(scoped css, body) for one component's page."""
    match = _STYLE.search(html or "")
    if not match:
        return "", str(html or "")
    return (scope_css(match.group("css"), wrapper),
            match.group("body"))


def _placed(view, regions):
    """(index, placement, rect) for each placement that has somewhere to go.

    Slots are assigned from the view rather than from what renders, so a
    component that fails leaves its slot empty instead of sliding the rest of
    the column upward -- a panel refreshing every 30s must not reflow itself
    every time an upstream blips.
    """
    valid = [(i, p) for i, p in enumerate(view.get("placements") or ())
             if isinstance(p, dict) and p.get("region") in regions]
    counts = {}
    for _, placement in valid:
        counts[placement["region"]] = counts.get(placement["region"], 0) + 1
    cut = {name: layout.slots(regions[name], n) for name, n in counts.items()}
    taken = {}
    for index, placement in valid:
        name = placement["region"]
        seat = taken[name] = taken.get(name, 0)
        taken[name] += 1
        yield index, placement, cut[name][seat]


def compose(view: dict, caps: dict, build_scene) -> str:
    """One page from a view's placements. Returns "" if nothing can be drawn.

    `build_scene(component, options, region_caps)` is injected: composing is
    arrangement, and what a component draws is the scene registry's business.
    """
    template = layout.template_of(view)
    regions = layout.regions(caps, template)
    styles, bodies = [], []
    for index, placement, rect in _placed(view, regions):
        wrapper = f"rg-{_safe(placement.get('id') or index)}"
        x, y, w, h = rect
        try:
            html = build_scene(placement.get("component"),
                               placement.get("options") or {},
                               {**caps, "w": w, "h": h})
        except Exception:                               # noqa: BLE001
            continue                                    # one region, not the page
        css, body = fragment(html, wrapper)
        # Absolute placement, because the regions are measured rectangles and
        # a flow layout would let one component's content move another's.
        styles.append(f"#{wrapper}{{position:absolute;left:{x}px;top:{y}px;"
                      f"width:{w}px;height:{h}px;overflow:hidden}}")
        styles.append(css)
        bodies.append(f'<div id="{wrapper}">{body}</div>')
    if not bodies:
        return ""
    width = int(caps.get("w") or 800)
    height = int(caps.get("h") or 480)
    return (f'<!doctype html><meta charset="utf-8"><style>{BASE_CSS}'
            f'html,body{{width:{width}px;height:{height}px;'
            f'position:relative;margin:0}}'
            f'{"".join(styles)}</style>{"".join(bodies)}')


def _safe(value) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", str(value))[:40] or "x"
