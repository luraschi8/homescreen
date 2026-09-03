"""The shop's day: aggregating it, and drawing it.

The provider's numbers were checked against the same store's ShopifyQL
`total_sales`: 19 orders summing to 870.06 EUR, exactly the figure the
merchant's own analytics page reports.
"""
import datetime
import pathlib
import tempfile

import pytest

from homescreen import fetch, scenes
from homescreen.reading import Reading
from homescreen.fetch.providers import shopify as provider

MADRID = "Europe/Madrid"


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Session:
    """One page of orders, or a canned error."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status
        self.calls = 0

    def post(self, *a, **k):
        self.calls += 1
        return _Resp(self._payload, self._status)


def _order(stamp, amount, fulfilled="UNFULFILLED", test=False, cancelled=None):
    return {"node": {"createdAt": stamp, "test": test, "cancelledAt": cancelled,
                     "displayFulfillmentStatus": fulfilled,
                     "currentTotalPriceSet": {"shopMoney": {"amount": amount}}}}


def _payload(edges, has_next=False, tz=MADRID):
    return {"data": {"shop": {"name": "Myalma", "currencyCode": "EUR",
                              "ianaTimezone": tz},
                     "orders": {"edges": edges,
                                "pageInfo": {"hasNextPage": has_next,
                                             "endCursor": "c1"}}}}


# --- the domain ---------------------------------------------------------------

@pytest.mark.parametrize("given,want", [
    ("myalma-es.myshopify.com", "myalma-es.myshopify.com"),
    ("https://myalma-es.myshopify.com/", "myalma-es.myshopify.com"),
    ("  MyAlma-ES.myshopify.com ", "myalma-es.myshopify.com"),
])
def test_a_domain_is_normalised(given, want):
    assert provider.clean_params({"shop": given}) == {"shop": want}


@pytest.mark.parametrize("bad", ["", "   ", "myalma", "a b.com", "x" * 200,
                                 "shop.com/../../etc"])
def test_a_domain_that_is_not_a_domain_is_refused(bad):
    # It reaches a URL, so it is checked rather than trusted.
    with pytest.raises(ValueError):
        provider.clean_params({"shop": bad})


def test_the_token_is_a_secret_not_a_parameter():
    # A store's whole order history is behind it, so it must never become part
    # of a job key or be stored beside the layout.
    assert provider.SECRETS == ("access_token",)
    assert "access_token" not in provider.clean_params(
        {"shop": "x.myshopify.com", "access_token": "shpat_leak"})


def test_no_token_is_an_error_rather_than_an_empty_day():
    with pytest.raises(ValueError):
        provider.fetch({"shop": "x.myshopify.com"}, session=_Session({}),
                       secrets={})


# --- the day ------------------------------------------------------------------

def _today_at(hour, minute=0):
    now = datetime.datetime.now(provider.ZoneInfo(MADRID))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_todays_orders_are_summed():
    edges = [_order(_today_at(9).isoformat(), "31.81"),
             _order(_today_at(10).isoformat(), "56.94"),
             _order(_today_at(11).isoformat(), "12.98")]
    got = provider.fetch({"shop": "x.myshopify.com"},
                         session=_Session(_payload(edges)),
                         secrets={"access_token": "t"})
    assert got["orders"] == 3
    assert got["total"] == pytest.approx(101.73)
    assert got["average"] == pytest.approx(33.91)
    assert got["currency"] == "EUR"


def test_an_order_just_after_local_midnight_counts_as_today():
    # A store in Madrid takes an order at 00:08 local, which is 22:08 the
    # previous day in UTC. Bucketing on the UTC date would leave the panel one
    # order behind the till.
    local = _today_at(0, 8)
    as_utc = local.astimezone(datetime.timezone.utc)
    got = provider.fetch(
        {"shop": "x.myshopify.com"},
        session=_Session(_payload([_order(
            as_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "32.00")])),
        secrets={"access_token": "t"})
    assert got["orders"] == 1, "the midnight order fell into yesterday"
    assert got["total"] == pytest.approx(32.00)


def test_test_and_cancelled_orders_are_not_takings():
    edges = [_order(_today_at(9).isoformat(), "50.00"),
             _order(_today_at(10).isoformat(), "99.00", test=True),
             _order(_today_at(11).isoformat(), "77.00",
                    cancelled=_today_at(12).isoformat())]
    got = provider.fetch({"shop": "x.myshopify.com"},
                         session=_Session(_payload(edges)),
                         secrets={"access_token": "t"})
    assert got["orders"] == 1 and got["total"] == pytest.approx(50.00)


def test_yesterday_is_counted_only_to_the_same_time_of_day():
    # Today is a partial day. Comparing it with a WHOLE yesterday would show a
    # shop losing badly every morning and catching up by midnight -- a number
    # that says more about when you looked than about how trade is going.
    now = datetime.datetime.now(provider.ZoneInfo(MADRID))
    yesterday = now - datetime.timedelta(days=1)
    early = yesterday.replace(hour=max(0, now.hour - 2), minute=0)
    late = yesterday.replace(hour=23, minute=59)
    edges = [_order(early.isoformat(), "40.00"),
             _order(late.isoformat(), "500.00")]
    got = provider.fetch({"shop": "x.myshopify.com"},
                         session=_Session(_payload(edges)),
                         secrets={"access_token": "t"})
    assert got["prev_total"] == pytest.approx(40.00), "late orders leaked in"


def test_unfulfilled_orders_are_counted():
    edges = [_order(_today_at(9).isoformat(), "10.00", "UNFULFILLED"),
             _order(_today_at(10).isoformat(), "10.00", "FULFILLED"),
             _order(_today_at(11).isoformat(), "10.00", "PARTIALLY_FULFILLED")]
    got = provider.fetch({"shop": "x.myshopify.com"},
                         session=_Session(_payload(edges)),
                         secrets={"access_token": "t"})
    assert got["unfulfilled"] == 2


def test_an_unreadable_amount_does_not_lose_the_day():
    edges = [_order(_today_at(9).isoformat(), "10.00"),
             _order(_today_at(10).isoformat(), None)]
    got = provider.fetch({"shop": "x.myshopify.com"},
                         session=_Session(_payload(edges)),
                         secrets={"access_token": "t"})
    assert got["total"] == pytest.approx(10.00)


def test_a_graphql_error_is_raised_not_drawn_as_zero():
    # GraphQL answers 200 with an `errors` array. Treating that as an empty day
    # would put a confident zero on the glass.
    session = _Session({"errors": [{"message": "Access denied"}]})
    with pytest.raises(ValueError, match="Access denied"):
        provider.fetch({"shop": "x.myshopify.com"}, session=session,
                       secrets={"access_token": "t"})


def test_a_rejected_token_says_so():
    session = _Session({}, status=401)
    with pytest.raises(ValueError, match="token"):
        provider.fetch({"shop": "x.myshopify.com"}, session=session,
                       secrets={"access_token": "bad"})


def test_paging_stops_rather_than_walking_a_whole_history():
    session = _Session(_payload([_order(_today_at(9).isoformat(), "1.00")],
                                has_next=True))
    provider.fetch({"shop": "x.myshopify.com"}, session=session,
                   secrets={"access_token": "t"})
    assert session.calls == provider.MAX_PAGES


# --- what it draws ------------------------------------------------------------

ENV = {"shop": "Myalma", "currency": "EUR", "orders": 19, "total": 870.06,
       "average": 45.79, "unfulfilled": 19, "prev_total": 702.30,
       "prev_orders": 15}


def _ctx(w, h, options=None, env=ENV):
    return scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": w, "h": h, "depth": 1}, now=1_788_500_000.0, device={},
        options=scenes.clean_options("shopify", {
            "shop": "x.myshopify.com", **(options or {})}),
        data=lambda _r: (Reading(data=env, ok=True, age_s=60.0) if env
                         else Reading.nothing()))


def test_money_is_written_the_way_a_spanish_shop_writes_it():
    from homescreen.scenes import shopify as scene
    assert scene.money(870.06, "EUR") == "870 €"
    assert scene.money(1234.5, "EUR") == "1.234 €"
    assert scene.money(45.79, "EUR", 2) == "45,79 €"
    assert scene.money(None, "EUR") == "—"


def test_a_comparison_with_a_day_that_sold_nothing_is_silent():
    # "Up from nothing" is division by zero dressed as insight, and the first
    # order of the day would read as an infinite rise.
    from homescreen.scenes import shopify as scene
    assert scene.delta(100, 0) == ("", "")
    assert scene.delta(100, None) == ("", "")
    assert scene.delta(124, 100) == ("up", "24%")
    assert scene.delta(76, 100) == ("down", "24%")


def test_the_takings_and_the_order_count_reach_the_glass():
    html = scenes.build("shopify", _ctx(417, 150)).html
    assert "870 €" in html
    assert "19 pedidos" in html


def test_one_order_is_not_pluralised():
    env = dict(ENV, orders=1, total=31.81)
    html = scenes.build("shopify", _ctx(417, 150, env=env)).html
    assert "1 pedido " in html and "1 pedidos" not in html


def test_the_euro_sign_becomes_EUR_on_the_round_panels_draw_list():
    # The device font carries 0x20-0xB0 and nothing else, so the shared
    # substitution table turns the symbol into letters rather than drawing a
    # box. The HTML path keeps the symbol.
    scene = scenes.build("shopify", _ctx(240, 240))
    assert any("EUR" in str(d.get("v")) for d in scene.components[0]["draw"])
    assert "€" in scenes.build("shopify", _ctx(417, 150)).html


def test_a_screen_with_no_shop_configured_says_so():
    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()),
        caps={"w": 417, "h": 150, "depth": 1}, now=1_788_500_000.0, device={},
        options=scenes.clean_options("shopify", {}))
    html = scenes.build("shopify", ctx).html
    assert "sin tienda" in html
    from homescreen.scenes._style import EMPTY_CLASS
    assert EMPTY_CLASS in html, "an unconfigured block must collapse"


def test_a_dead_feed_says_to_check_the_token_rather_than_showing_zero():
    html = scenes.build("shopify", _ctx(417, 150, env=None)).html
    assert "sin datos" in html
    assert "870" not in html


def test_the_comparison_can_be_turned_off():
    on = scenes.build("shopify", _ctx(417, 150)).html
    off = scenes.build("shopify", _ctx(417, 150, {"show_compare": False})).html
    assert "vs ayer" in on and "vs ayer" not in off


def test_it_draws_on_every_surface_it_declares():
    for w, h in ((764, 62), (127, 62), (417, 150), (321, 335)):
        scene = scenes.build("shopify", _ctx(w, h))
        assert scene.html and "870" in scene.html, (w, h)
        assert scene.components[0]["draw"], (w, h)


def test_the_round_panel_gets_a_draw_list_too():
    drawn = scenes.build("shopify", _ctx(240, 240)).components[0]["draw"]
    values = [d.get("v") for d in drawn]
    assert any("870" in str(v) for v in values), values


def test_it_needs_nothing_until_a_shop_is_named():
    from homescreen.scenes import shopify as scene
    assert scene.needs({}, {}) == ()
    assert scene.needs({"shop": "x.myshopify.com"}, {})[0]["provider"] == "shopify"
