"""Emit the golden layout fixture both resolvers are pinned against.

`homescreen/draw.py` draws the preview an operator judges by; the firmware's
`ui::drawlist` draws the glass. They must agree, and a disagreement is a bug you
can only see by holding a browser next to a panel. So the Python produces the
cases and the expected placements, and the C++ suite asserts against them.

Same idea as scripts/dump_wire_fixture.py: generated, checked in, and pinned
from both sides.
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from homescreen import draw          # noqa: E402

#: Every case exists because it could differ between two implementations:
#: rounding, fallbacks, clamping, truncation, unicode, ordering.
CASES = [
    ("a clock on the round panel", 240, 240, [
        draw.text("center", "22:53", "xl"),
        draw.text("below", "Madrid", "sm", "dim"),
        draw.text("rim_bottom", "BS AS 17:53", "xs", "dim")]),
    ("the same clock on the e-paper", 800, 480, [
        draw.text("center", "22:53", "xl"),
        draw.text("below", "Madrid", "sm", "dim"),
        draw.text("rim_bottom", "BS AS 17:53", "xs", "dim")]),
    ("every slot, so the whole table is pinned", 240, 240, [
        draw.text("rim_top", "top", "xs"),
        draw.text("above", "above", "sm"),
        draw.text("center", "centre", "md"),
        draw.text("below", "below", "lg"),
        draw.text("rim_bottom", "bottom", "xl")]),
    ("every tone", 240, 240, [
        draw.text("above", "normal", "sm", "normal"),
        draw.text("center", "dim", "sm", "dim"),
        draw.text("below", "good", "sm", "good"),
        draw.text("rim_bottom", "bad", "sm", "bad")]),
    ("an unknown slot and size fall back, not vanish", 240, 240, [
        {"t": "text", "slot": "nowhere", "v": "fallback", "size": "enormous"}]),
    ("an unknown tone becomes normal", 240, 240, [
        draw.text("center", "x", "md", "chartreuse")]),
    ("unknown instructions are dropped", 240, 240, [
        {"t": "hologram", "v": "no"},
        draw.text("center", "yes", "md")]),
    ("a tiny panel clamps to the legibility floor", 40, 40, [
        draw.text("center", "tiny", "xs")]),
    ("a very wide panel scales on its short side", 800, 200, [
        draw.text("center", "wide", "xl")]),
    ("odd dimensions, where rounding could differ", 241, 241, [
        draw.text("center", "odd", "xl"),
        draw.text("rim_top", "odd", "sm")]),
    ("utf-8 text survives both parsers", 240, 240, [
        draw.text("center", "21°C", "lg"),
        draw.text("below", "Málaga · nublado", "xs", "dim")]),
    ("an empty string draws nothing", 240, 240, [
        draw.text("center", "", "xl"),
        draw.text("below", "kept", "sm")]),
    # The shape cases. Until these existed the whole vocabulary could be
    # deleted with both suites green, and the firmware read fractions as ints
    # -- so every shape landed at (0,0) with radius 1 and the panel showed a
    # speck where an icon was meant to be.
    ("shapes are fractions of the panel, not pixels", 240, 240, [
        draw.circle(0.5, 0.5, 0.25, "warn"),
        draw.line(0.1, 0.9, 0.9, 0.9, "dim", 0.02),
        draw.tri([(0.25, 0.8), (0.35, 0.8), (0.30, 0.9)], "good")]),
    ("the same shapes on the e-paper scale off the short side", 800, 480, [
        draw.circle(0.5, 0.5, 0.25, "warn"),
        draw.line(0.1, 0.9, 0.9, 0.9, "dim", 0.02),
        draw.tri([(0.25, 0.8), (0.35, 0.8), (0.30, 0.9)], "good")]),
    ("a whole icon, expanded server-side", 240, 240,
        draw.icon("sun", 0.5, 0.35, 0.22, "hot")),
    ("an unfilled circle stays unfilled", 240, 240, [
        draw.circle(0.5, 0.5, 0.3, "cool", fill=False)]),
    ("shape rounding on odd dimensions", 241, 241, [
        draw.circle(0.5, 0.5, 0.25, "normal"),
        draw.line(0.0, 0.5, 1.0, 0.5, "normal", 0.01)]),
    ("a degenerate radius still draws something", 240, 240, [
        draw.circle(0.5, 0.5, 0.0, "bad")]),
    ("a triangle without three points is dropped", 240, 240, [
        {"t": "tri", "p": [0.1, 0.1, 0.2, 0.2], "tone": "good"},
        draw.text("center", "kept", "sm")]),
    ("a blank screen is a positive instruction", 240, 240, [
        draw.fill("off")]),
    ("a fill can be a background with content over it", 240, 240, [
        draw.fill("off"),
        draw.text("center", "22:53", "xl")]),
    ("a coloured fill is not the off one", 240, 240, [
        draw.fill("bad")]),
    ("every tone reaches a shape, not just text", 240, 240, [
        draw.circle(0.2, 0.2, 0.05, "normal"),
        draw.circle(0.4, 0.2, 0.05, "dim"),
        draw.circle(0.6, 0.2, 0.05, "good"),
        draw.circle(0.8, 0.2, 0.05, "bad"),
        draw.circle(0.2, 0.6, 0.05, "accent"),
        draw.circle(0.4, 0.6, 0.05, "warn"),
        draw.circle(0.6, 0.6, 0.05, "cool"),
        draw.circle(0.8, 0.6, 0.05, "hot")]),
]


def build(out_path: pathlib.Path) -> None:
    cases = []
    for name, w, h, instructions in CASES:
        cases.append({
            "name": name, "w": w, "h": h,
            "draw": instructions,
            "expect": draw.resolve(instructions, w, h),
        })
    lines = [
        "// GENERATED by HomeScreen/scripts/dump_draw_parity.py -- do not edit.",
        "//",
        "// The layout homescreen/draw.py produces. ui::drawlist must produce",
        "// exactly the same, because one draws the preview and the other draws",
        "// the glass. Regenerate with:",
        "//   venv/bin/python scripts/dump_draw_parity.py",
        "#pragma once",
        "",
        f"inline constexpr char kParityCases[] = R\"JSON({json.dumps(cases, separators=(',', ':'), ensure_ascii=False)})JSON\";",
        "",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path,
                    default=ROOT / "firmware" / "test" / "fixtures_draw.h")
    args = ap.parse_args()
    build(args.out)
    print(f"wrote {args.out} ({len(CASES)} cases)")
