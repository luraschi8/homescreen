"""Share and currency prices, from Finnhub.

One symbol per request is this vendor's shape, so a screen tracking five
tickers is five jobs sharing a cadence -- which is exactly what the job model
is for, and why the component does not have to know it.

Nothing depends on the vendor: another is a module declaring the same four
things. The payload is normalised here so a swap does not touch a component.
"""

from __future__ import annotations

NAME = "quotes"

PARAMS = (
    {"key": "symbol", "label": "Símbolo", "type": "text"},
)

#: Markets move continuously but a desk display is not a trading terminal, and
#: the free tier is 60 calls a minute across every symbol. A minute keeps five
#: tickers at five calls a minute with room to spare.
DEFAULT_INTERVAL_S = 60

#: One request a second, which the free tier permits comfortably and which
#: keeps a five-symbol screen from arriving as a burst.
MIN_SPACING_S = 1.0

SECRETS = ("api_key",)

ENDPOINT = "https://finnhub.io/api/v1/quote"
TIMEOUT_S = (3.05, 8)

#: Symbols are uppercase alphanumerics plus a few separators used by exchange
#: prefixes and FX pairs (`BINANCE:BTCUSDT`, `OANDA:EUR_USD`).
_ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:_-^")


def clean_params(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    symbol = str(raw.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("hace falta un símbolo")
    if len(symbol) > 24 or set(symbol) - _ALLOWED:
        # The symbol reaches a URL. Refusing here beats discovering it in a
        # request, and a symbol that is not a symbol cannot be a typo worth
        # fetching forever.
        raise ValueError(f"símbolo no válido: {symbol}")
    return {"symbol": symbol}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    key = (secrets or {}).get("api_key")
    if not key:
        raise ValueError("falta la clave de Finnhub")
    if session is None:
        import requests
        session = requests.Session()
    resp = session.get(ENDPOINT, timeout=TIMEOUT_S,
                       params={"symbol": params["symbol"], "token": key})
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("respuesta inesperada de Finnhub")
    price = _num(body.get("c"))
    if price is None or price == 0:
        # Finnhub answers an unknown symbol with a 200 and every field zero.
        # Drawing that would put "0.00" on the glass and call the feed healthy,
        # which is indistinguishable from a stock that really is worthless.
        raise ValueError(f"sin cotización para {params['symbol']}")
    previous = _num(body.get("pc"))
    change = None
    if previous:
        change = (price - previous) / previous * 100.0
    return {"symbol": params["symbol"], "price": price,
            "previous_close": previous, "change_pct": change,
            "high": _num(body.get("h")), "low": _num(body.get("l"))}


def _num(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None
