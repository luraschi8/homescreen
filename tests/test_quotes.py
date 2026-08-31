"""Prices, and the surface-adaptation rule this component was written for.

"It can show multiple tickers in a larger layout for the dashboard and rotate
the ticker for a small screen." One options set, two presentations, and the
cycling is the component's own business -- there is no platform feature for it.
"""
import pathlib
import tempfile

import pytest

from homescreen import fetch, scenes
from homescreen.reading import Reading

PRICES = {"AAPL": (227.4, 1.24), "MSFT": (415.2, -0.42),
          "BINANCE:BTCUSDT": (63120.0, 2.9)}
THREE = "AAPL, MSFT, BINANCE:BTCUSDT"
EPAPER = {"w": 800, "h": 480, "depth": 1}
ROUND = {"w": 240, "h": 240, "depth": 16, "shape": "round"}


def _data(req):
    symbol = req["params"]["symbol"]
    price, change = PRICES.get(symbol, (10.0, 0.5))
    return Reading(data={"symbol": symbol, "price": price,
                         "change_pct": change}, ok=True)


def build(caps, now=0.0, symbols=THREE, rotate=8, data=_data):
    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=caps, now=now,
        device={}, options={"symbols": symbols, "rotate_s": rotate}, data=data)
    return scenes.build("quotes", ctx)


def drawn(*a, **kw):
    return [d["v"] for d in build(*a, **kw).components[0]["draw"]]


# --- the two presentations --------------------------------------------------

def test_a_wide_panel_shows_every_symbol_at_once():
    values = drawn(EPAPER)
    assert len(values) == 3
    assert all(any(s in v for v in values) for s in ("AAPL", "MSFT", "BTCUSDT"))


def test_a_small_round_panel_shows_one_at_a_time():
    values = drawn(ROUND)
    assert "AAPL" in values and "MSFT" not in " ".join(values)
    assert "1/3" in values, "and says which of how many"


def test_the_small_panel_moves_on():
    assert drawn(ROUND, now=0)[0] == "AAPL"
    assert drawn(ROUND, now=9)[0] == "MSFT"
    assert drawn(ROUND, now=17)[0] == "BINANCE:BTCUSDT"
    assert drawn(ROUND, now=25)[0] == "AAPL", "and wraps"


def test_which_symbol_is_showing_is_a_function_of_the_clock():
    # Not of a position the device remembers. That is what lets the preview
    # show any moment and the device hold no state -- the same reasoning the
    # schedule uses.
    assert drawn(ROUND, now=100_000)[0] == drawn(ROUND, now=100_024)[0]


def test_the_device_is_woken_when_the_shown_symbol_changes():
    # Not on a fixed tick: the trick the clock uses for the minute boundary.
    assert build(ROUND, now=0).poll_s == 8
    assert build(ROUND, now=6).poll_s == 2


def test_the_decision_measures_the_line_not_the_panel_size():
    # `BINANCE:BTCUSDT 63,120 ▲ 2.90%` needs three times the width of
    # `AAPL 227.40`, and a rule about height cannot see that -- which is how a
    # round panel ended up stacking three lines that ran off both edges.
    long_names = drawn(ROUND, symbols=THREE)
    assert "1/3" in long_names, "too wide to stack"
    # A wide panel fits the same list comfortably.
    assert len(drawn(EPAPER, symbols=THREE)) == 3


def test_one_symbol_never_shows_a_counter():
    assert not any("/" in v for v in drawn(ROUND, symbols="AAPL"))


# --- what it says when it has nothing ---------------------------------------

def test_with_no_data_it_draws_dashes_rather_than_a_price_of_zero():
    # Every symbol keeps its row and shows a dash. It used to collapse to one
    # bare "--" because the old flat width ratio said three rows could not fit
    # across a 240px circle; measured as a chord they can, and "AAPL --" says
    # more than "--" does.
    values = drawn(ROUND, data=lambda req: Reading.nothing())
    assert values, "something is drawn"
    assert all("--" in v for v in values), values
    assert any("AAPL" in v for v in values), values
    assert not any("0.00" in v or " 0 " in v for v in values), values


def test_with_no_symbols_it_says_so():
    assert any("sin simbolos" in v for v in drawn(ROUND, symbols=""))


# --- options ----------------------------------------------------------------

def test_symbols_are_deduplicated_and_bounded():
    from homescreen.scenes import quotes
    assert quotes.symbols_of({"symbols": "aapl, AAPL , msft"}) == ("AAPL", "MSFT")
    many = ",".join(f"SYM{i}" for i in range(30))
    assert len(quotes.symbols_of({"symbols": many})) == quotes.MAX_SYMBOLS


def test_each_symbol_is_its_own_fetch():
    # The vendor's shape, not the component's choice -- and it is what lets
    # two screens tracking AAPL share one job.
    needs = scenes.needs("quotes", {"symbols": THREE}, {})
    assert len(needs) == 3
    assert {n["params"]["symbol"] for n in needs} == set(PRICES)


def test_two_screens_tracking_the_same_symbol_share_one_job():
    plan = fetch.derive({"a": {"scene": "quotes", "options": {"symbols": "AAPL"}},
                         "b": {"scene": "quotes", "options": {"symbols": "AAPL"}}},
                        {})
    assert len(plan) == 1


def test_a_rise_and_a_fall_are_toned_differently():
    scene = build(EPAPER)
    tones = {d["v"][:4]: d["tone"] for d in scene.components[0]["draw"]}
    assert tones["AAPL"] == "good" and tones["MSFT"] == "bad"


# --- the provider -----------------------------------------------------------

def test_an_unknown_symbol_is_a_failure_not_a_price_of_zero():
    # Finnhub answers an unknown symbol with a 200 and every field zero.
    # Drawing that puts "0.00" on the glass and calls the feed healthy.
    from homescreen.fetch.providers import quotes as provider

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"c": 0, "h": 0, "l": 0, "pc": 0}

    class Session:
        def get(self, *a, **k):
            return Resp()

    with pytest.raises(ValueError, match="sin cotización"):
        provider.fetch({"symbol": "NOPE"}, session=Session(),
                       secrets={"api_key": "k"})


def test_the_change_is_computed_from_the_previous_close():
    from homescreen.fetch.providers import quotes as provider

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"c": 110.0, "pc": 100.0, "h": 111.0, "l": 99.0}

    class Session:
        def get(self, *a, **k):
            return Resp()

    got = provider.fetch({"symbol": "X"}, session=Session(),
                         secrets={"api_key": "k"})
    assert got["change_pct"] == pytest.approx(10.0)


@pytest.mark.parametrize("bad", ["", "   ", "A" * 30, "<script>", "a b"])
def test_a_symbol_that_is_not_a_symbol_is_refused(bad):
    # It reaches a URL, and a typo is not worth fetching forever.
    with pytest.raises(ValueError):
        fetch.providers.clean_params("quotes", {"symbol": bad})


def test_the_provider_refuses_to_fetch_without_its_key():
    from homescreen.fetch.providers import quotes as provider
    with pytest.raises(ValueError, match="clave"):
        provider.fetch({"symbol": "AAPL"}, secrets={})
