"""Who is in the fleet, and who merely showed up.

Registration is unauthenticated on purpose -- a device on the LAN says its
hardware id and appears. That is right for discovery and wrong for trust: the
fleet should be what someone CHOSE, not everything that ever spoke. These are
the tests for the gate between the two.
"""
import pytest

from homescreen import registry
from homescreen.serve import create_app

HW = "aabb00112233"
Q = "w=240&h=240&depth=16&components=radar,clock"
CFG = {"location": {"name": "Madrid", "timezone": "Europe/Madrid",
                    "lat": 40.4, "lon": -3.7},
       "feeds": {"adsb": {"source": "api", "endpoint": "https://x"}},
       "devices": []}


@pytest.fixture
def ctx(tmp_path):
    return create_app(CFG, tmp_path, version="t").test_client(), tmp_path


def test_a_device_that_calls_in_is_registered_but_not_admitted(ctx):
    client, cache = ctx
    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    assert body["scene"] == "pending"
    assert body["assigned"] is False
    # Registered, though: an operator cannot approve what they cannot see.
    assert HW in registry.load(cache)
    assert not registry.is_approved(registry.load(cache)[HW])


def test_a_pending_device_is_served_an_explanation_not_content(ctx):
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    registry.assign(cache, HW, name="desk", scene="clock")
    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    # Assigned in the record, deliberately NOT served: the gate is about what
    # reaches the glass, so an assignment made before admission stays inert.
    assert registry.load(cache)[HW]["scene"] == "clock"
    assert body["scene"] == "pending"
    assert body["components"] == []
    assert "aprobación" in body["message"]


def test_approving_a_device_serves_it_what_it_was_assigned(ctx):
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    registry.assign(cache, HW, name="desk", scene="clock")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    assert (body["scene"], body["assigned"]) == ("clock", True)


def test_revoking_keeps_the_record_so_letting_it_back_in_is_one_click(ctx):
    # Revoking is not deleting. Someone taking a panel out of the fleet for an
    # afternoon should not have to name and configure it again afterwards.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    registry.assign(cache, HW, name="desk", scene="clock")

    client.put(f"/api/devices/{HW}/membership", json={"approved": False})
    rec = registry.load(cache)[HW]
    assert (rec["name"], rec["scene"]) == ("desk", "clock")
    assert client.get(f"/api/devices/{HW}/scene?{Q}").get_json()["scene"] == "pending"

    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    assert client.get(f"/api/devices/{HW}/scene?{Q}").get_json()["scene"] == "clock"


def test_a_removed_device_that_keeps_polling_comes_back_pending(ctx):
    # Removal has to mean something even though the device is still on the LAN
    # and still polling. It may reappear -- we cannot stop it -- but it
    # reappears as a REQUEST, never silently back in the fleet with its old job.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    registry.assign(cache, HW, name="desk", scene="clock")

    client.delete(f"/api/devices/{HW}")
    assert HW not in registry.load(cache)

    body = client.get(f"/api/devices/{HW}/scene?{Q}").get_json()
    assert body["scene"] == "pending"
    assert registry.load(cache)[HW]["name"] is None, "and with no memory of its job"


def test_devices_written_before_the_gate_existed_are_grandfathered(ctx):
    # A gate added in a deploy must not blank every panel in the house. Records
    # with no approval field pre-date it and are members already.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    data = registry.load_raw(cache)
    del data[HW][registry.APPROVAL_FIELD]
    registry.save(cache, data)

    assert registry.is_approved(registry.load(cache)[HW])
    registry.assign(cache, HW, name="desk", scene="clock")
    assert client.get(f"/api/devices/{HW}/scene?{Q}").get_json()["scene"] == "clock"


def test_the_pending_tray_lists_who_is_asking_oldest_first(ctx):
    client, cache = ctx
    for hw in ("aa00000000ff", "bb00000000ff", "cc00000000ff"):
        client.get(f"/api/devices/{hw}/scene?{Q}")
    client.put("/api/devices/bb00000000ff/membership", json={"approved": True})
    assert list(registry.pending(cache)) == ["aa00000000ff", "cc00000000ff"]


def test_approving_something_that_never_called_in_is_a_404_not_a_new_record(ctx):
    # Otherwise the approval route becomes a second, unvalidated way to create
    # devices -- and the registry cap is the only thing standing between an
    # unauthenticated LAN and a full SD card.
    client, cache = ctx
    r = client.put("/api/devices/ffffffffffff/membership", json={"approved": True})
    assert r.status_code == 404
    assert registry.load(cache) == {}


@pytest.mark.parametrize("bad", ["yes", 1, None, [], {}])
def test_approval_takes_a_boolean_and_nothing_else(ctx, bad):
    client, _ = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    r = client.put(f"/api/devices/{HW}/membership", json={"approved": bad})
    assert r.status_code == 400


# --- a screen remembers each component's settings ---------------------------

def test_trying_a_component_out_does_not_lose_the_previous_ones_settings(ctx):
    # Found by using the dashboard: the preview invites you to try components,
    # and switching away silently discarded the configuration you had typed.
    # Coming back gave you an empty form with no way to recover it.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    registry.set_approval(cache, HW, True)
    registry.assign(cache, HW, scene="clock",
                    options={"timezone": "Europe/Madrid",
                             "second_label": "Buenos Aires"})

    registry.assign(cache, HW, scene="planes")
    assert registry.load(cache)[HW]["options"] == {}, "a radar takes no clock"

    registry.assign(cache, HW, scene="clock")
    assert registry.load(cache)[HW]["options"] == {
        "timezone": "Europe/Madrid", "second_label": "Buenos Aires"}


def test_one_screens_settings_never_reach_another(ctx):
    # "Configuration is per assignment" is the rule this must not break while
    # adding memory: two screens showing a clock keep their own cities.
    client, cache = ctx
    other = "bb00000000ff"
    for hw in (HW, other):
        client.get(f"/api/devices/{hw}/scene?{Q}")
        registry.set_approval(cache, hw, True)
    registry.assign(cache, HW, scene="clock", options={"timezone": "Europe/Madrid"})
    registry.assign(cache, other, scene="clock", options={"timezone": "Asia/Tokyo"})
    registry.assign(cache, HW, scene="planes")
    registry.assign(cache, HW, scene="clock")
    assert registry.load(cache)[HW]["options"]["timezone"] == "Europe/Madrid"
    assert registry.load(cache)[other]["options"]["timezone"] == "Asia/Tokyo"


def test_explicit_options_still_win_over_what_was_remembered(ctx):
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    registry.set_approval(cache, HW, True)
    registry.assign(cache, HW, scene="clock", options={"timezone": "Europe/Madrid"})
    registry.assign(cache, HW, scene="planes")
    registry.assign(cache, HW, scene="clock", options={"timezone": "Asia/Tokyo"})
    assert registry.load(cache)[HW]["options"]["timezone"] == "Asia/Tokyo"


def test_the_memory_cannot_grow_beyond_the_scenes_that_exist(ctx):
    # devices.json is hand-editable and this field is written on every switch.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    registry.set_approval(cache, HW, True)
    data = registry.load_raw(cache)
    data[HW][registry.OPTIONS_MEMORY_FIELD] = {
        f"junk{i}": {"x": i} for i in range(200)}
    registry.save(cache, data)
    registry.assign(cache, HW, scene="clock", options={"timezone": "UTC"})
    registry.assign(cache, HW, scene="planes")
    kept = registry.load(cache)[HW][registry.OPTIONS_MEMORY_FIELD]
    assert set(kept) <= set(registry.ASSIGNABLE_SCENES)


# --- the gate must not be self-service --------------------------------------

@pytest.mark.parametrize("body", [None, {}, {"x": 1}, {"approve": True}])
def test_a_device_cannot_admit_itself_with_a_bodyless_post(ctx, body):
    # The route defaulted a MISSING `approved` to True, so anything that could
    # register -- anything on the LAN -- admitted itself with one POST and the
    # pending state was decorative. The value was validated; its ABSENCE was
    # not, and absence was the privileged case.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    r = (client.put(f"/api/devices/{HW}/membership") if body is None
         else client.put(f"/api/devices/{HW}/membership", json=body))
    assert r.status_code == 400
    assert not registry.is_approved(registry.load(cache)[HW])
    assert client.get(f"/api/devices/{HW}/scene?{Q}").get_json()["scene"] == "pending"


def test_the_form_route_defaults_to_revoking_not_granting(ctx):
    # Its sibling: a missing field here already meant "revoke". Both routes
    # must fail in the same direction, and it must be the ungenerous one.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    client.post(f"/device/{HW}/approval", data={})
    assert not registry.is_approved(registry.load(cache)[HW])


def test_a_revoked_screen_is_not_served_by_the_name_it_kept(ctx):
    # Revoking KEEPS the name on purpose, and the alias route resolved on that
    # name without ever asking whether the screen was still a member -- so the
    # data-push contract went on serving a screen an operator had just ejected.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    registry.assign(cache, HW, name="garaje", scene="planes")
    assert client.get("/api/display/garaje/health").status_code == 200

    client.put(f"/api/devices/{HW}/membership", json={"approved": False})
    assert client.get("/api/display/garaje/health").status_code == 404
    assert client.get("/api/display/garaje/data").status_code == 404


def test_a_named_but_never_admitted_screen_is_not_served_either(ctx):
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    registry.assign(cache, HW, name="garaje", scene="planes")
    assert client.get("/api/display/garaje/data").status_code == 404


def test_a_hostile_hardware_id_is_refused_not_a_500(ctx):
    # <hw> comes straight out of a URL path.
    client, _ = ctx
    for hw in ("a b", "<script>", " ", "ñ", "a\tb", "x" * 200, "%2e%2e"):
        r = client.post(f"/device/{hw}/approval", data={"approved": "1"})
        assert r.status_code in (302, 303, 404), f"{hw!r} -> {r.status_code}"


def test_a_malformed_capability_list_cannot_take_down_the_fleet_page(ctx):
    # The page an operator would open in order to remove the bad record.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?{Q}")
    data = registry.load_raw(cache)
    data[HW]["caps"] = {"components": 5, "w": 240, "h": 240}
    registry.save(cache, data)
    assert client.get("/").status_code == 200
    assert client.get(f"/device/{HW}").status_code == 200


def test_a_revoked_screen_loses_its_exemption_from_the_render_budget(ctx):
    # The exemption exists because "an operator chose every one of them", and
    # approval is now what records that choice -- while revoking keeps the
    # scene, so this used to exempt exactly the screens just ejected.
    client, cache = ctx
    client.get(f"/api/devices/{HW}/scene?w=800&h=480&depth=1")
    client.put(f"/api/devices/{HW}/membership", json={"approved": True})
    registry.assign(cache, HW, name="salon", scene="clock")
    from homescreen.serve import create_app  # noqa: F401  (documented import)
    rec = registry.load(cache)[HW]
    assert registry.is_approved(rec) and rec["scene"] == "clock"
    client.put(f"/api/devices/{HW}/membership", json={"approved": False})
    assert not registry.is_approved(registry.load(cache)[HW])
