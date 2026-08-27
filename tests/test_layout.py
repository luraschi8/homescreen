"""Where a component goes on a screen.

The property worth protecting: the 240x240 panel and the composed e-paper are
the same structure with different numbers. If the small screen ever needs a
special case, the model is wrong.
"""
import pytest

from homescreen import layout

ROUND = {"w": 240, "h": 240, "depth": 16}
EPD = {"w": 800, "h": 480, "depth": 1}
COMPONENTS = {"clock", "planes", "status"}


def test_the_round_panel_is_the_degenerate_case_not_a_special_case():
    assert layout.surface_name(ROUND) == "round_240"
    assert list(layout.regions(ROUND)) == ["full"]
    assert layout.regions(ROUND)["full"]["holds"] == 1


def test_the_epaper_is_divided_the_way_the_spec_measured_it():
    names = layout.regions(EPD)
    assert set(names) == {"masthead", "main_left", "main_right", "markets"}
    assert names["markets"]["rect"] == (18, 406, 764, 62)
    assert names["markets"]["holds"] == 6, "the band fits five tickers plus FX"


def test_an_unknown_panel_still_works_without_a_table_edit():
    # A new screen size must not need code to show anything at all. It gets
    # the degenerate surface, which is what every device does today.
    odd = {"w": 320, "h": 170, "depth": 16}
    assert layout.surface_name(odd) == layout.FALLBACK_SURFACE
    assert layout.regions(odd)["full"]["rect"] == (0, 0, 320, 170)


def test_a_component_is_handed_its_regions_geometry_not_the_panels():
    # The only thing a component needs to know, and it already knows how to
    # use it -- this is the same value it gets as 240x240 today.
    caps = layout.region_caps(EPD, "markets")
    assert (caps["w"], caps["h"]) == (764, 62)
    assert caps["depth"] == 1, "depth is the hardware's, not the rectangle's"


def test_a_placement_in_a_region_this_glass_lacks_is_dropped_not_relocated():
    # Moving it would invent a layout nobody chose.
    got = layout.clean_placement(
        {"region": "markets", "component": "clock"}, ROUND, COMPONENTS)
    assert got is None


def test_a_placement_naming_an_unknown_component_is_dropped():
    assert layout.clean_placement(
        {"region": "full", "component": "ghost"}, ROUND, COMPONENTS) is None


def test_a_regions_capacity_is_enforced():
    crowded = {"placements": [
        {"region": "markets", "component": "clock"} for _ in range(9)]}
    view = layout.clean_view(crowded, EPD, COMPONENTS)
    assert len(view["placements"]) == 6


def test_placements_keep_their_order():
    view = layout.clean_view({"placements": [
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
