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

#: What a section heading costs the block under it: SPEC SS9's 10px label, its
#: 1px rule, and the gaps. Deducted BEFORE the component is measured -- it is
#: told the height it will actually be rendered into, not the slot's.
#:
#: Measured, not estimated: the label box renders 16px and the body starts at
#: 18. The 17 this used to be was a guess that under-reserved by a pixel.
HEADING_PX = 18

#: The fragment's own box inside the region. A component's page is NOT the
#: region: the region may also carry a heading, and `scope_css` rewrites a
#: fragment's `html,body` rule to whatever this maps to. Pointing it at the
#: wrapper let a fragment set the WRAPPER's height -- and since the composer
#: emits its positioning rule first, the fragment's `height:105px` beat the
#: region's `height:122px` at equal specificity and clipped the last row off
#: every labelled block.
FRAGMENT_CLASS = "rg-frag"

#: What a region that has nothing to show is allowed to keep: one line to say
#: so, and its heading if it has one.
#:
#: CLAUDE.md §6: "Sections collapse when empty -- never an empty rectangle
#: sitting on the panel for six hours." AGENDA with no calendar configured was
#: 13.5% of the glass at 1.76% ink -- the largest block on the panel, reserved
#: to say "sin calendario". The space goes back to the siblings that have
#: something to put in it.
COLLAPSED_PX = 20

#: Interior padding for every region on a composed page, as a fraction of the
#: panel's short side.
#:
#: The panel margin belongs to the TEMPLATE -- the region rects already carry
#: it -- so a component's own padding is breathing room inside a box that is
#: already inset. Left to each region it was derived from that region's short
#: side, which gave the 53px masthead 3px and the 335px column 18px, so the
#: date sat 14px outside the clock beneath it. Measured on the live panel:
#: five different left edges (4, 18, 22, 25) and three right ones.
#:
#: Set by the composer, after each fragment's own stylesheet, because the
#: composer is what knows there is a panel margin at all. A scene rendered on
#: its own -- the round display, a preview -- keeps its region-derived padding,
#: which is right, because there no template has inset anything.
PAD_SHARE = 0.025


def scope_css(css: str, wrapper: str, root: str | None = None) -> str:
    """Prefix every selector so a fragment's styles cannot leave its region.

    A small, testable string transform rather than a CSS parser: the input is
    what our own `page()` produced, and the alternative -- asking every
    component to prefix its own classes -- is a convention that fails silently
    the first time somebody forgets.
    """
    root = root or f"#{wrapper}"
    out = []
    for block in _split_blocks(css):
        selector, _, rest = block.partition("{")
        if not rest:
            continue
        selector = selector.strip()
        if selector.startswith("@"):
            # A media or font rule: keep the wrapper, scope what is inside it.
            inner = rest.rsplit("}", 1)[0]
            out.append(f"{selector}{{{scope_css(inner, wrapper, root)}}}")
            continue
        scoped = ", ".join(
            root if part.strip() in _ROOT_SELECTORS
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
    return (scope_css(match.group("css"), wrapper,
                      f"#{wrapper} .{FRAGMENT_CLASS}"),
            match.group("body"))


def _build(build_scene, placement, caps):
    """Call the injected builder, telling it WHICH placement this is.

    The id decides which credential a component's data was fetched under, and
    therefore which cached payload to read back. Passed as an optional argument
    so a caller that does not care -- every test, and the preview -- keeps its
    three-argument builder.
    """
    component = placement.get("component")
    options = placement.get("options") or {}
    try:
        return build_scene(component, options, caps,
                           placement_id=placement.get("id"))
    except TypeError:
        return build_scene(component, options, caps)


def _collapsible(html: str) -> bool:
    """Whether this fragment is a component saying it has nothing to show.

    Read off the marker `_style.empty()` puts on its own output rather than
    asked of the component, because `empty()` is the only place an empty state
    is rendered -- so there is nowhere for a component to forget to say it.
    """
    from homescreen.scenes._style import EMPTY_CLASS
    return EMPTY_CLASS in (html or "")


def _placed(view, regions, collapsed=None, dropped=None):
    """(index, placement, rect) for each placement that has somewhere to go.

    `collapsed` maps a placement's index to the exact size it should take
    instead of its share -- how a section with nothing to show gives its space
    back to the siblings that have something to put in it.

    Slots are assigned from the view rather than from what renders, so a
    component that fails leaves its slot empty instead of sliding the rest of
    the column upward -- a panel refreshing every 30s must not reflow itself
    every time an upstream blips.
    """
    dropped = dropped or set()
    valid = [(i, p) for i, p in enumerate(view.get("placements") or ())
             if isinstance(p, dict) and p.get("region") in regions
             and i not in dropped]
    counts = {}
    for _, placement in valid:
        counts[placement["region"]] = counts.get(placement["region"], 0) + 1
    # The view's own proportions, in the order its placements appear.
    asked = {}
    for _, placement in valid:
        asked.setdefault(placement["region"], []).append(
            placement.get("weight"))
    # Per region, in placement order, the sizes that are no longer shares.
    collapsed = collapsed or {}
    fixed = {}
    for index, placement in valid:
        fixed.setdefault(placement["region"], []).append(
            collapsed.get(index))
    cut = {name: layout.slots(regions[name], n, asked.get(name),
                              fixed=fixed.get(name))
           for name, n in counts.items()}
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

    # Built once to find out which regions have nothing to show, then again
    # with those collapsed. A component is asked what it would draw at its
    # provisional size; whether it has DATA does not depend on that size, and
    # building a scene is arithmetic, not rendering.
    collapsed, dropped, probes = {}, set(), {}
    for index, placement, rect in _placed(view, regions):
        heading = str(placement.get("label") or "")
        inner = max(1, rect[3] - HEADING_PX) if heading else rect[3]
        try:
            probe = _build(build_scene, placement,
                           {**caps, "w": rect[2], "h": inner})
        except Exception:                               # noqa: BLE001
            continue
        # Kept against the rect it was built for, so the second pass rebuilds
        # only what actually moved. When nothing collapses -- the common case
        # -- every placement is built exactly once.
        probes[index] = (rect, probe)
        if _collapsible(probe):
            region = regions.get(placement.get("region")) or {}
            if region.get("stack") == "h":
                # A band divides ACROSS, so a collapsed cell is a 20px-wide
                # splinter that still tries to set type in it -- "sin uso"
                # came out as one letter per line. Across, an empty cell goes
                # away entirely and its width joins its neighbours.
                dropped.add(index)
            else:
                collapsed[index] = COLLAPSED_PX + (HEADING_PX if heading else 0)

    # One number for every region, from the panel rather than from each
    # region's own geometry.
    pad = max(2, round(min(int(caps.get("w") or 800),
                           int(caps.get("h") or 480)) * PAD_SHARE))
    # Unless it would empty a band completely: a row of nothing says less
    # than a row saying why it is empty.
    by_region = {}
    for index, placement, _rect in _placed(view, regions):
        by_region.setdefault(placement.get("region"), []).append(index)
    for members in by_region.values():
        if members and all(i in dropped for i in members):
            dropped.difference_update(members)

    styles, bodies = [], []
    for index, placement, rect in _placed(view, regions, collapsed, dropped):
        wrapper = f"rg-{_safe(placement.get('id') or index)}"
        x, y, w, h = rect
        heading = str(placement.get("label") or "")
        # Measured AFTER the heading is taken off. A component asked to lay
        # out for 335px and then rendered into 318 makes every one of its size
        # decisions against a rectangle that does not exist.
        inner = max(1, h - HEADING_PX) if heading else h
        was, ready = probes.get(index, (None, None))
        if was == rect and ready is not None:
            html = ready                                # unmoved: already built
        else:
            try:
                html = _build(build_scene, placement,
                              {**caps, "w": w, "h": inner})
            except Exception:                           # noqa: BLE001
                continue                                # one region, not the page
        css, body = fragment(html, wrapper)
        # The heading is drawn by the COMPOSER, not the component: a component
        # asked to render its own title would need to know it had one, and the
        # same calendar is "agenda" in one column and "cumpleanos" in another.
        # The fragment ALWAYS gets its own box, heading or not, because its
        # own stylesheet sizes that box and must never size the region.
        body = f'<div class="{FRAGMENT_CLASS}">{body}</div>'
        if heading:
            body = f'<div class="rg-label">{_escape(heading)}</div>{body}'
        # Absolute placement, because the regions are measured rectangles and
        # a flow layout would let one component's content move another's.
        styles.append(f"#{wrapper}{{position:absolute;left:{x}px;top:{y}px;"
                      f"width:{w}px;height:{h}px;overflow:hidden}}")
        # Before the fragment's own rules: a component that sizes its root
        # agrees with this (it was measured for `inner`), and one that does
        # not still gets the right box.
        styles.append(f"#{wrapper} .{FRAGMENT_CLASS}{{height:{inner}px;"
                      f"overflow:hidden}}")
        if heading:
            # SPEC SS9's section label: 10px, 500, .14em tracking, over a rule.
            styles.append(
                f"#{wrapper} .rg-label{{font-size:10px;font-weight:500;"
                f"letter-spacing:.14em;text-transform:uppercase;"
                f"border-top:1px solid #000;padding-top:3px;margin-bottom:2px;"
                # Indented to the same edge as the block beneath it. The rule
                # spans the region, the words line up with the content.
                f"padding-left:{pad}px;"
                f"font-family:Inter,'DejaVu Sans',sans-serif}}")
        styles.append(css)
        # AFTER the fragment's own rule, at equal specificity, so this wins.
        styles.append(f"#{wrapper} .{FRAGMENT_CLASS}{{--pad:{pad}px;"
                      f"--pad-sm:{max(1, round(pad * 0.4))}px}}")
        bodies.append(f'<div id="{wrapper}">{body}</div>')
    bodies.extend(_cell_rules(view, regions, collapsed, pad, dropped))
    if not bodies:
        return ""
    bodies.extend(_rules(layout.TEMPLATES.get(template) or {}, caps))
    width = int(caps.get("w") or 800)
    height = int(caps.get("h") or 480)
    return (f'<!doctype html><meta charset="utf-8"><style>{BASE_CSS}'
            f'html,body{{width:{width}px;height:{height}px;'
            f'position:relative;margin:0}}'
            f'{"".join(styles)}</style>{"".join(bodies)}')


def _cell_rules(view, regions, collapsed, pad, dropped=None) -> list:
    """A hairline between neighbouring cells of a horizontal region.

    v6 gives every ticker cell a `border-left`; in 1-bit they were dropped
    rather than translated, so six values floated in the markets band with no
    boundaries at all. Drawn by the COMPOSER for the same reason the section
    headings are: a line between two placements is not either one's decoration,
    and a component drawing its own would not know it was the first.
    """
    out = []
    for name, region in regions.items():
        if region.get("stack") != "h":
            continue
        seats = [(i, p, r) for i, p, r in _placed(view, {name: region},
                                                  collapsed, dropped)]
        if len(seats) < 2:
            continue
        _, _, (_, top, _, height) = seats[0]
        # Short of the full band, so the rules read as separators rather than
        # as a second grid crossing the one the template already draws.
        inset = max(2, round(height * 0.12))
        for _index, _placement, (x, _y, _w, _h) in seats[1:]:
            out.append(f'<div class="rg-cell-rule" style="position:absolute;'
                       f'left:{x - pad // 2}px;top:{top + inset}px;width:1px;'
                       f'height:{max(1, height - 2 * inset)}px;'
                       f'background:#000"></div>')
    return out


def _rules(spec: dict, caps: dict) -> list:
    """The template's own lines, in pixels.

    The design's whole structure is carried by these -- under the masthead,
    down the gutter, above the markets band -- and the composer drew none of
    them. A composed page was blocks of text floating side by side, and the
    only full-width line on it was a component's internal rule leaking out of
    its region.

    They belong to the TEMPLATE because a line between two regions is not any
    component's decoration.
    """
    from homescreen import surface
    screen = surface.describe(caps)
    w, h = int(screen.get("w") or 0), int(screen.get("h") or 0)
    if not w or not h:
        return []
    out = []
    for x1, y1, x2, y2 in spec.get("rules") or ():
        left, top = round(x1 * w), round(y1 * h)
        # One pixel in the thin direction, exactly. A rule two pixels wide on
        # 1-bit glass is twice the ink and twice the ghosting.
        width = max(1, round((x2 - x1) * w))
        height = max(1, round((y2 - y1) * h))
        out.append(f'<div class="rg-rule" style="position:absolute;'
                   f'left:{left}px;top:{top}px;width:{width}px;'
                   f'height:{height}px;background:#000"></div>')
    return out


def _escape(value: str) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _safe(value) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", str(value))[:40] or "x"
