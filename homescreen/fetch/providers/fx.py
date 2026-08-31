"""Currency rates, from Frankfurter (European Central Bank reference rates).

Finnhub's forex endpoint answers 403 on the free tier -- verified against the
real key -- so quotes covers shares and crypto but not currency. This covers
currency, needs no credential at all, and is the ECB's own daily fixing, which
is the right number for "what is the euro doing" and the wrong one for trading.
Nobody should be trading off a desk ornament.
"""

from __future__ import annotations

NAME = "fx"

PARAMS = (
    {"key": "base", "label": "Moneda base", "type": "text", "default": "EUR"},
    {"key": "symbols", "label": "Contra", "type": "text", "default": "USD"},
)

#: The ECB publishes once a working day, around 16:00 CET. Polling faster is
#: asking a question whose answer cannot have changed.
DEFAULT_INTERVAL_S = 3600
MIN_SPACING_S = 0.5
SECRETS: tuple = ()

ENDPOINT = "https://api.frankfurter.app/latest"
TIMEOUT_S = (3.05, 8)

MAX_SYMBOLS = 8
_CODE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _codes(raw, limit=MAX_SYMBOLS) -> tuple:
    out = []
    for part in str(raw or "").replace(";", ",").split(","):
        code = part.strip().upper()
        if len(code) == 3 and not (set(code) - _CODE) and code not in out:
            out.append(code)
    return tuple(out[:limit])


def clean_params(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    base = _codes(raw.get("base") or "EUR", limit=1)
    if not base:
        raise ValueError("la moneda base debe ser un código de 3 letras")
    against = _codes(raw.get("symbols") or "USD")
    against = tuple(c for c in against if c != base[0])
    if not against:
        raise ValueError("hace falta al menos una moneda distinta de la base")
    return {"base": base[0], "symbols": ",".join(against)}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    if session is None:
        import requests
        session = requests.Session()
    resp = session.get(ENDPOINT, timeout=TIMEOUT_S,
                       params={"from": params["base"],
                               "to": params["symbols"]})
    resp.raise_for_status()
    body = resp.json()
    rates = body.get("rates") if isinstance(body, dict) else None
    if not isinstance(rates, dict) or not rates:
        raise ValueError("la respuesta no trae tipos de cambio")
    return {"base": body.get("base") or params["base"],
            "date": str(body.get("date") or ""),
            "rates": {str(k): float(v) for k, v in rates.items()
                      if isinstance(v, (int, float))}}
