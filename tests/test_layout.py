"""Where a component goes on a screen.

The property worth protecting: the 240x240 panel and the composed e-paper are
the same structure with different numbers. If the small screen ever needs a
special case, the model is wrong.
"""
import pytest

from homescreen import layout

# `shape` is DECLARED, not inferred: a 240x240 panel could equally be square
# glass, and guessing wrong lays a seam across a circle.
ROUND = {"w": 240, "h": 240, "depth": 16, "shape": "round"}
EPD = {"w": 800, "h": 480, "depth": 1}
COMPONENTS = {"clock", "planes", "status"}


def test_the_round_panel_is_not_an_exception_just_a_screen_where_one_fits():
    # It was tempting to call this "the degenerate case". It is not a case at
    # all: the same question is asked of every screen -- which templates does
    # your glass carry -- and a small round panel happens to answer with one.
    assert layout.templates_for(ROUND) == ("single",)
    assert list(layout.regions(ROUND)) == ["full"]


def test_the_dashboard_template_reproduces_the_geometry_the_spec_measured():
    # The proportions are SPEC 9's 800x480 layout as fractions. If they ever
    # stop resolving to the measured pixels on that panel, the design drawn
    # for it has been quietly redrawn.
    r = layout.regions(EPD, "dashboard")
    assert r["masthead"]["rect"] == (0, 0, 800, 53)
    assert r["main_left"]["rect"] == (18, 63, 417, 335)
    assert r["main_right"]["rect"] == (461, 63, 321, 335)
    assert r["markets"]["rect"] == (18, 406, 764, 62)
    assert r["markets"]["holds"] == 6, "the band fits five tickers plus FX"


def test_the_same_template_resolves_on_a_screen_nobody_has_bought():
    # The whole point of fractions: a new size is not a code edit.
    big = {"w": 1024, "h": 600, "depth": 16}
    assert "dashboard" in layout.templates_for(big)
    r = layout.regions(big, "dashboard")
    assert r["masthead"]["rect"] == (0, 0, 1024, 66)
    assert r["markets"]["rect"][2] > 900, "it scaled, it did not clip"


def test_glass_too_small_for_a_template_is_not_offered_it():
    # Not because we know what a 128x64 is, but because its bands would be
    # under the legible floor.
    badge = {"w": 128, "h": 64, "depth": 1}
    assert layout.templates_for(badge) == ("single",)


def test_a_round_screen_is_never_offered_a_seam_across_it():
    assert "split" not in layout.templates_for(ROUND)
    assert "dashboard" not in layout.templates_for(ROUND)


def test_every_screen_can_always_show_one_thing():
    # No device may end up with nothing to choose, whatever its shape.
    for caps in (ROUND, EPD, {"w": 320, "h": 170, "depth": 16},
                 {"w": 1, "h": 1, "depth": 1}, {}):
        assert layout.DEFAULT_TEMPLATE in layout.templates_for(caps)


def test_a_placement_in_a_region_this_glass_lacks_is_dropped_not_relocated():
    # Moving it would invent a layout nobody chose.
    got = layout.clean_placement(
        {"region": "markets", "component": "clock"}, ROUND, COMPONENTS)
    assert got is None


def test_a_placement_naming_an_unknown_component_is_dropped():
    assert layout.clean_placement(
        {"region": "full", "component": "ghost"}, ROUND, COMPONENTS) is None


def test_a_regions_capacity_is_enforced():
    crowded = {"template": "dashboard", "placements": [
        {"region": "markets", "component": "clock"} for _ in range(9)]}
    view = layout.clean_view(crowded, EPD, COMPONENTS)
    assert len(view["placements"]) == 6


def test_placements_keep_their_order():
    view = layout.clean_view({"template": "dashboard", "placements": [
        {"region": "main_left", "component": "clock"},
        {"region": "main_left", "component": "planes"},
        {"region": "main_left", "component": "status"}]}, EPD, COMPONENTS)
    assert [p["component"] for p in view["placements"]] == \
        ["clock", "planes", "status"]


def test_a_record_written_before_views_existed_reads_as_what_it_always_meant():
    # No migration write. Rewriting every record on deploy to change nothing
    # observable is a whole-registry write for no benefit, and a failure mode
    # for devices.json at exactly the moment nobody is watching.
    view = layout.view_for({"scene": "clock",
                            "options": {"timezone": "Europe/Madrid"}})
    assert view["placements"] == [{"id": "full-clock", "region": "full",
                                   "component": "clock",
                                   "options": {"timezone": "Europe/Madrid"}}]


def test_a_record_with_views_uses_its_schedule_default():
    rec = {"views": {"a": layout.single("clock"), "b": layout.single("planes")},
           "schedule": {"default": "b", "slots": []}}
    assert layout.view_for(rec)["placements"][0]["component"] == "planes"


def test_a_named_view_can_be_asked_for_directly():
    rec = {"views": {"a": layout.single("clock"), "b": layout.single("planes")},
           "schedule": {"default": "b"}}
    assert layout.view_for(rec, "a")["placements"][0]["component"] == "clock"


@pytest.mark.parametrize("rec", [
    None, {}, [], "x", {"views": "not a dict"}, {"views": {}},
    {"views": {"a": None}}, {"scene": None}, {"schedule": None},
])
def test_reading_a_view_never_raises(rec):
    # This is on the path a panel depends on, over a hand-editable file.
    assert isinstance(layout.view_for(rec), dict)
    assert "placements" in layout.view_for(rec)


def test_the_number_of_placements_is_bounded():
    many = {"placements": [{"region": "full", "component": "clock"}
                           for _ in range(500)]}
    assert len(layout.clean_view(many, ROUND, COMPONENTS)["placements"]) <= 1


# --- the seam, end to end ---------------------------------------------------

def _app(tmp_path):
    from homescreen.serve import create_app
    cfg = {"location": {"name": "Madrid", "timezone": "Europe/Madrid",
                        "lat": 40.4, "lon": -3.7},
           "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
           "devices": []}
    return create_app(cfg, tmp_path, version="t").test_client()


HW = "aabb00112233"
Q = "w=240&h=240&depth=16&components=radar,clock"


def test_a_view_and_the_legacy_shape_serve_the_identical_wire(tmp_path):
    # The whole claim of this step: the record can grow a layout without one
    # byte changing on the way to a device. If these ever differ, the
    # migration has become a behaviour change and needs its own release.
    from homescreen import registry
    client = _app(tmp_path)
    client.get(f"/api/devices/{HW}/scene?{Q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    registry.assign(tmp_path, HW, name="salon", scene="clock",
                    options={"timezone": "Europe/Madrid"})
    legacy = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()

    data = registry.load_raw(tmp_path)
    data[HW]["views"] = {"principal": layout.single(
        "clock", {"timezone": "Europe/Madrid"})}
    data[HW]["schedule"] = {"default": "principal", "slots": []}
    registry.save(tmp_path, data)
    composed = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()

    assert composed == legacy


def test_a_views_options_are_the_ones_that_reach_the_device(tmp_path):
    # And they are not the record's legacy `options`, which is how you would
    # discover the seam is decorative.
    from homescreen import registry
    client = _app(tmp_path)
    client.get(f"/api/devices/{HW}/scene?{Q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    registry.assign(tmp_path, HW, name="salon", scene="clock",
                    options={"timezone": "Europe/Madrid"})
    data = registry.load_raw(tmp_path)
    data[HW]["views"] = {"tokio": layout.single("clock",
                                                {"timezone": "Asia/Tokyo"})}
    data[HW]["schedule"] = {"default": "tokio", "slots": []}
    registry.save(tmp_path, data)

    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    drawn = [d["v"] for d in body["components"][0]["draw"]]
    # The clock names the city from the zone it was given, so this is the
    # view's option arriving -- not the record's legacy `options`, which still
    # say Europe/Madrid. If the seam were decorative, this would read Madrid.
    assert any("Tokyo" in text for text in drawn), drawn
    assert not any("Madrid" in text for text in drawn), drawn


def test_a_device_can_declare_its_shape_and_the_registry_keeps_it(tmp_path):
    from homescreen import registry
    client = _app(tmp_path)
    client.get(f"/api/devices/{HW}/scene?w=240&h=240&depth=16&shape=round"
               "&components=radar,draw_list")
    caps = registry.load(tmp_path)[HW]["caps"]
    assert caps["shape"] == "round"
    assert layout.templates_for(caps) == ("single",), \
        "round glass is never offered a layout with corners"


@pytest.mark.parametrize("declared", ["oval", "", "ROUND", "<script>", "1"])
def test_an_unrecognised_shape_is_dropped_rather_than_stored(tmp_path, declared):
    from homescreen import registry
    client = _app(tmp_path)
    client.get(f"/api/devices/{HW}/scene?w=240&h=240&depth=16&shape={declared}"
               "&components=draw_list")
    caps = registry.load(tmp_path)[HW]["caps"]
    assert caps.get("shape") in (None, "round"), caps


# --- Stacked regions ---------------------------------------------------------
#
# `holds` and `stack` were in the template model from the first commit, but
# nothing subdivided a region, so every placement in one was handed the same
# rectangle. These pin the arithmetic that fixes it.

def test_a_region_holding_one_placement_gives_it_the_whole_rectangle():
    region = layout.regions(EPD, "dashboard")["main_left"]
    assert layout.slots(region, 1) == [region["rect"]]


def test_a_vertical_stack_tiles_its_region_exactly():
    region = layout.regions(EPD, "dashboard")["main_left"]
    x, y, w, h = region["rect"]
    got = layout.slots(region, 4)
    assert [r[0] for r in got] == [x] * 4          # column keeps its left edge
    assert [r[2] for r in got] == [w] * 4          # and its width
    assert got[0][1] == y                          # starts at the top
    assert sum(r[3] for r in got) == h             # no gap, no overlap
    for before, after in zip(got, got[1:]):
        assert before[1] + before[3] == after[1]   # each abuts the next


def test_a_horizontal_stack_tiles_its_region_exactly():
    region = layout.regions(EPD, "dashboard")["markets"]
    x, y, w, h = region["rect"]
    got = layout.slots(region, 6)
    assert [r[1] for r in got] == [y] * 6
    assert [r[3] for r in got] == [h] * 6
    assert sum(r[2] for r in got) == w
    for before, after in zip(got, got[1:]):
        assert before[0] + before[2] == after[0]


def test_leftover_pixels_go_to_the_leading_slots_rather_than_vanishing():
    # 62px of height over 4 slots is 15.5 each. Slots must still tile 62.
    region = {"rect": (0, 0, 100, 62), "stack": "v", "holds": 4}
    got = layout.slots(region, 4)
    assert [r[3] for r in got] == [16, 16, 15, 15]
    assert sum(r[3] for r in got) == 62


def test_a_region_with_no_stack_axis_still_divides_rather_than_piling():
    # `single`'s `full` declares stack None because it holds one thing. If a
    # stored view ever names it twice, overlapping is the one wrong answer.
    region = {"rect": (0, 0, 240, 240), "stack": None, "holds": 1}
    got = layout.slots(region, 2)
    assert got[0] != got[1]
    assert sum(r[3] for r in got) == 240


# --- weighted slots ----------------------------------------------------------
#
# SPEC §9's markets band is not six equal cells: an FX box at flex 1.55 and
# five tickers at 1 each. Equal division cannot express the original design.

def test_weights_divide_a_region_in_the_declared_proportions():
    region = {"rect": (0, 0, 655, 62), "stack": "h", "holds": 6,
              "weights": (1.55, 1, 1, 1, 1, 1)}
    got = layout.slots(region, 6)
    widths = [r[2] for r in got]
    assert sum(widths) == 655, widths
    assert widths[0] > widths[1], "the FX box is the wide one"
    assert abs(widths[0] / widths[1] - 1.55) < 0.05, widths


def test_weights_still_tile_exactly_with_an_awkward_remainder():
    region = {"rect": (0, 0, 100, 40), "stack": "h", "holds": 3,
              "weights": (1, 1, 1)}
    got = layout.slots(region, 3)
    assert sum(r[2] for r in got) == 100
    for before, after in zip(got, got[1:]):
        assert before[0] + before[2] == after[0]


def test_fewer_placements_than_weights_uses_the_leading_weights():
    # Two tickers in a six-cell band still put the FX box first and give it
    # its share, rather than falling back to halves.
    region = {"rect": (0, 0, 655, 62), "stack": "h", "holds": 6,
              "weights": (1.55, 1, 1, 1, 1, 1)}
    got = layout.slots(region, 2)
    assert sum(r[2] for r in got) == 655
    assert abs(got[0][2] / got[1][2] - 1.55) < 0.05


def test_a_region_with_no_weights_still_divides_evenly():
    region = {"rect": (0, 0, 300, 40), "stack": "h", "holds": 3}
    assert [r[2] for r in layout.slots(region, 3)] == [100, 100, 100]


def test_the_dashboard_markets_band_matches_the_original_sketch():
    # SPEC §9: FX box 181px, each ticker ~117px, across an inner width of 764.
    spec = layout.regions(EPD, "dashboard")["markets"]
    widths = [r[2] for r in layout.slots(spec, 6)]
    assert sum(widths) == spec["rect"][2]
    assert abs(widths[0] - 181) <= 3, widths
    for ticker in widths[1:]:
        assert abs(ticker - 117) <= 3, widths
