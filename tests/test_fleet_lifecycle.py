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
    body = client.get(f"/api/device/{HW}/scene?{Q}").get_json()
    assert body["scene"] == "pending"
    assert body["assigned"] is False
    # Registered, though: an operator cannot approve what they cannot see.
    assert HW in registry.load(cache)
    assert not registry.is_approved(registry.load(cache)[HW])


def test_a_pending_device_is_served_an_explanation_not_content(ctx):
    client, cache = ctx
    client.get(f"/api/device/{HW}/scene?{Q}")
    registry.assign(cache, HW, name="desk", scene="clock")
    body = client.get(f"/api/device/{HW}/scene?{Q}").get_json()
    # Assigned in the record, deliberately NOT served: the gate is about what
    # reaches the glass, so an assignment made before admission stays inert.
    assert registry.load(cache)[HW]["scene"] == "clock"
    assert body["scene"] == "pending"
    assert body["components"] == []
    assert "aprobación" in body["message"]


def test_approving_a_device_serves_it_what_it_was_assigned(ctx):
    client, cache = ctx
    client.get(f"/api/device/{HW}/scene?{Q}")
    registry.assign(cache, HW, name="desk", scene="clock")
    client.post(f"/api/devices/{HW}/approval", json={"approved": True})
    body = client.get(f"/api/device/{HW}/scene?{Q}").get_json()
    assert (body["scene"], body["assigned"]) == ("clock", True)


def test_revoking_keeps_the_record_so_letting_it_back_in_is_one_click(ctx):
    # Revoking is not deleting. Someone taking a panel out of the fleet for an
    # afternoon should not have to name and configure it again afterwards.
    client, cache = ctx
    client.get(f"/api/device/{HW}/scene?{Q}")
    client.post(f"/api/devices/{HW}/approval", json={"approved": True})
    registry.assign(cache, HW, name="desk", scene="clock")

    client.post(f"/api/devices/{HW}/approval", json={"approved": False})
    rec = registry.load(cache)[HW]
    assert (rec["name"], rec["scene"]) == ("desk", "clock")
    assert client.get(f"/api/device/{HW}/scene?{Q}").get_json()["scene"] == "pending"

    client.post(f"/api/devices/{HW}/approval", json={"approved": True})
    assert client.get(f"/api/device/{HW}/scene?{Q}").get_json()["scene"] == "clock"


def test_a_removed_device_that_keeps_polling_comes_back_pending(ctx):
    # Removal has to mean something even though the device is still on the LAN
    # and still polling. It may reappear -- we cannot stop it -- but it
    # reappears as a REQUEST, never silently back in the fleet with its old job.
    client, cache = ctx
    client.get(f"/api/device/{HW}/scene?{Q}")
    client.post(f"/api/devices/{HW}/approval", json={"approved": True})
    registry.assign(cache, HW, name="desk", scene="clock")

    client.delete(f"/api/devices/{HW}")
    assert HW not in registry.load(cache)

    body = client.get(f"/api/device/{HW}/scene?{Q}").get_json()
    assert body["scene"] == "pending"
    assert registry.load(cache)[HW]["name"] is None, "and with no memory of its job"


def test_devices_written_before_the_gate_existed_are_grandfathered(ctx):
    # A gate added in a deploy must not blank every panel in the house. Records
    # with no approval field pre-date it and are members already.
    client, cache = ctx
    client.get(f"/api/device/{HW}/scene?{Q}")
    data = registry.load_raw(cache)
    del data[HW][registry.APPROVAL_FIELD]
    registry.save(cache, data)

    assert registry.is_approved(registry.load(cache)[HW])
    registry.assign(cache, HW, name="desk", scene="clock")
    assert client.get(f"/api/device/{HW}/scene?{Q}").get_json()["scene"] == "clock"


def test_the_pending_tray_lists_who_is_asking_oldest_first(ctx):
    client, cache = ctx
    for hw in ("aa00000000ff", "bb00000000ff", "cc00000000ff"):
        client.get(f"/api/device/{hw}/scene?{Q}")
    client.post("/api/devices/bb00000000ff/approval", json={"approved": True})
    assert list(registry.pending(cache)) == ["aa00000000ff", "cc00000000ff"]


def test_approving_something_that_never_called_in_is_a_404_not_a_new_record(ctx):
    # Otherwise the approval route becomes a second, unvalidated way to create
    # devices -- and the registry cap is the only thing standing between an
    # unauthenticated LAN and a full SD card.
    client, cache = ctx
    r = client.post("/api/devices/ffffffffffff/approval", json={"approved": True})
    assert r.status_code == 404
    assert registry.load(cache) == {}


@pytest.mark.parametrize("bad", ["yes", 1, None, [], {}])
def test_approval_takes_a_boolean_and_nothing_else(ctx, bad):
    client, _ = ctx
    client.get(f"/api/device/{HW}/scene?{Q}")
    r = client.post(f"/api/devices/{HW}/approval", json={"approved": bad})
    assert r.status_code == 400
