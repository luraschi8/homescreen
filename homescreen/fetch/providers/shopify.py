"""Today's takings from a Shopify store.

The Admin GraphQL API has no sum, so the day is aggregated here: every order
created since the start of YESTERDAY, bucketed by the shop's own calendar day.
Verified against the same store's ShopifyQL `total_sales` -- 19 orders summing
to 870.06 EUR, exactly the figure the analytics page reports -- so this is the
number the merchant already recognises rather than a near-miss of our own.

Yesterday comes along for the ride because one number has no size: "870 today"
means nothing until you know whether that is a good Tuesday, and a second day
costs one more page of the same request.

Yesterday is counted only up to THE SAME TIME OF DAY. Today is a partial day
and yesterday is a whole one, so comparing them directly would show a shop
losing badly every morning and catching up by midnight -- a number that says
more about when you looked than about how trade is going.

The shop's OWN timezone decides where the day starts. A store in Madrid takes
an order at 00:08 local, which is 22:08 the previous day in UTC, and counting
that against yesterday would put the panel one order behind the till.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NAME = "shopify"

PARAMS = (
    {"key": "shop", "label": "Dominio de la tienda", "type": "text",
     "placeholder": "mi-tienda.myshopify.com"},
)

#: Takings move all day and a shop owner looks often. Five minutes is inside
#: the cadence a person notices and nowhere near Shopify's rate limits: the
#: Admin API is a leaky bucket of 1000 points restoring at 50/s, and this
#: query costs a few dozen.
DEFAULT_INTERVAL_S = 300

MIN_SPACING_S = 1.0

#: The app's own credentials, exchanged for a token per the CLIENT CREDENTIALS
#: grant. Admin-created custom apps were deprecated in January 2026 and no
#: longer issue a static `shpat_` token, so there is nothing to paste: an app
#: in the Dev Dashboard has an id and a secret, and Shopify hands back a token
#: that lives 24 hours.
#:
#: `access_token` is still accepted for a store that has a legacy one.
#:
#: A store's whole order history is behind these, so they are credentials and
#: never parameters -- job keys are built from parameters.
SECRETS = ("client_id", "client_secret", "access_token")

TIMEOUT_S = (3.05, 12)

#: Shopify dates this; pinned so a new release cannot change the response shape
#: under a panel nobody is watching.
API_VERSION = "2024-10"

#: Shopify's own figure for a client-credentials token is `expires_in: 86399`.
#: Renewed early by this margin, because a token that expires between the
#: check and the request is a failed fetch for no reason.
TOKEN_MARGIN_S = 600

#: Live tokens, by (shop, client id). In memory ONLY, and deliberately: it is
#: valid for a day, the fetch daemon is long-lived, and a token written to disk
#: is a second credential at rest to protect. Losing it on restart costs one
#: extra request.
_TOKENS: dict = {}

#: Orders per page, and how many pages we will walk. 250 is the API's own
#: ceiling; five pages is 1250 orders in two days, past which a dashboard
#: showing a running total is the wrong tool anyway.
PAGE = 250
MAX_PAGES = 5

_QUERY = """
query DayTakings($q: String!, $first: Int!, $after: String) {
  shop { name currencyCode ianaTimezone }
  orders(first: $first, after: $after, query: $q, sortKey: CREATED_AT) {
    edges {
      node {
        createdAt
        test
        cancelledAt
        displayFulfillmentStatus
        currentTotalPriceSet { shopMoney { amount } }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def clean_params(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    shop = str(raw.get("shop") or "").strip().lower()
    if shop.startswith("https://"):
        shop = shop[8:]
    if shop.startswith("http://"):
        shop = shop[7:]
    shop = shop.strip("/")
    if not shop:
        raise ValueError("hace falta el dominio de la tienda")
    if len(shop) > 120 or any(c not in _ALLOWED for c in shop):
        raise ValueError(f"dominio no válido: {shop}")
    if "." not in shop:
        raise ValueError("el dominio va completo: mi-tienda.myshopify.com")
    return {"shop": shop}


#: The domain reaches a URL, so it is checked rather than trusted.
_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789.-")


def access_token(shop: str, secrets: dict, session, *, force: bool = False) -> str:
    """A live Admin API token for this shop.

    A legacy static token is used as given. Otherwise the app's id and secret
    are exchanged for one, and the result is kept until shortly before it
    expires -- re-exchanging on every fetch would be a second request every
    five minutes for a credential good for a day.
    """
    secrets = secrets or {}
    static = str(secrets.get("access_token") or "").strip()
    if static:
        return static

    client_id = str(secrets.get("client_id") or "").strip()
    client_secret = str(secrets.get("client_secret") or "").strip()
    if not (client_id and client_secret):
        raise ValueError("faltan las credenciales de la app de Shopify")

    import time
    key = (shop, client_id)
    held = _TOKENS.get(key)
    if held and not force and held[1] - time.time() > TOKEN_MARGIN_S:
        return held[0]

    resp = session.post(
        f"https://{shop}/admin/oauth/access_token",
        json={"client_id": client_id, "client_secret": client_secret,
              "grant_type": "client_credentials"},
        headers={"Content-Type": "application/json"}, timeout=TIMEOUT_S)
    if resp.status_code in (400, 401, 403):
        raise ValueError("Shopify rechazó el id o el secreto de la app")
    resp.raise_for_status()
    body = resp.json()
    token = (body or {}).get("access_token") if isinstance(body, dict) else None
    if not token:
        raise ValueError("Shopify no devolvió un token")
    try:
        lifetime = float((body or {}).get("expires_in") or 0)
    except (TypeError, ValueError):
        lifetime = 0.0
    # An absent or absurd lifetime is treated as one hour rather than as
    # forever: re-exchanging hourly is cheap, and a stale token is a dead panel.
    if not 60.0 <= lifetime <= 172800.0:
        lifetime = 3600.0
    _TOKENS[key] = (str(token), time.time() + lifetime)
    return str(token)


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    if session is None:
        import requests
        session = requests.Session()

    shop = params["shop"]
    token = access_token(shop, secrets, session)
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token,
               "Content-Type": "application/json"}

    # The window has to be built in the shop's zone, which the first response
    # tells us -- so the first page is asked for with a deliberately generous
    # window and narrowed once the zone is known. Two days of orders either
    # way is a page or two, not a scan of the history.
    since_utc = datetime.now(ZoneInfo("UTC")) - timedelta(days=3)
    variables = {"q": f"created_at:>={since_utc:%Y-%m-%dT%H:%M:%SZ}",
                 "first": PAGE, "after": None}

    orders, shop_info = [], {}
    for _page in range(MAX_PAGES):
        try:
            body = _call(session, url, headers, variables)
        except _Unauthorised:
            # The token expired early, or the secret was rotated. Exchange a
            # fresh one and try once more; a second refusal is a real problem
            # and becomes a message an operator can act on, because this one
            # reaches the status page.
            headers["X-Shopify-Access-Token"] = access_token(
                shop, secrets, session, force=True)
            try:
                body = _call(session, url, headers, variables)
            except _Unauthorised:
                raise ValueError(
                    "Shopify rechazó el token recién emitido "
                    "(¿falta el permiso read_orders?)") from None
        shop_info = body.get("shop") or shop_info
        block = body.get("orders") or {}
        orders.extend(edge.get("node") or {}
                      for edge in (block.get("edges") or []))
        info = block.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        variables["after"] = info.get("endCursor")

    zone = _zone(shop_info.get("ianaTimezone"))
    now = datetime.now(zone)
    yesterday = now.date() - timedelta(days=1)

    live = [o for o in orders if not o.get("test") and not o.get("cancelledAt")]
    return {
        "shop": shop_info.get("name") or shop,
        "currency": shop_info.get("currencyCode") or "",
        "tz": str(getattr(zone, "key", "")),
        "as_of": now.strftime("%H:%M"),
        **_day(live, now.date(), zone),
        # Yesterday to this same minute, so the two are the same length of day.
        **{f"prev_{k}": v
           for k, v in _day(live, yesterday, zone, until=now.time()).items()},
    }


def _day(orders, day, zone, until=None) -> dict:
    """`{orders, total, average, unfulfilled}` for one calendar day.

    `until` cuts the day off at a time of day, which is what makes yesterday
    comparable with a today that is still happening.
    """
    on_day = []
    for order in orders:
        when = _local(order.get("createdAt"), zone)
        if when is None or when.date() != day:
            continue
        if until is not None and when.time() > until:
            continue
        on_day.append(order)
    total = 0.0
    for order in on_day:
        amount = (((order.get("currentTotalPriceSet") or {})
                   .get("shopMoney") or {}).get("amount"))
        try:
            total += float(amount)
        except (TypeError, ValueError):
            continue                    # one unreadable order, not the day
    count = len(on_day)
    unfulfilled = sum(1 for o in on_day
                      if str(o.get("displayFulfillmentStatus") or "").upper()
                      in ("UNFULFILLED", "PARTIALLY_FULFILLED"))
    return {"orders": count, "total": round(total, 2),
            "average": round(total / count, 2) if count else 0.0,
            "unfulfilled": unfulfilled}


def _local(stamp, zone):
    """An order's creation time in the SHOP's zone, or None.

    A store in Madrid takes an order at 00:08 local, which is 22:08 the
    previous day in UTC. Bucketing on the UTC date would put that order against
    yesterday and leave the panel one behind the till.
    """
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(zone)
    except ValueError:
        return None


def _zone(name):
    try:
        return ZoneInfo(str(name))
    except Exception:                                   # noqa: BLE001
        # Without the shop's own zone the day starts in the wrong place, which
        # is wrong by a few orders rather than wrong by a lot. The server's is
        # the closest guess available, and it is where the screen is.
        return datetime.now().astimezone().tzinfo


class _Unauthorised(Exception):
    """The token was refused. Recoverable exactly once, by getting a new one."""


def _call(session, url, headers, variables) -> dict:
    resp = session.post(url, json={"query": _QUERY, "variables": variables},
                        headers=headers, timeout=TIMEOUT_S)
    if resp.status_code in (401, 403):
        raise _Unauthorised()
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError("respuesta inesperada de Shopify")
    errors = body.get("errors")
    if errors:
        # GraphQL answers 200 with an errors array. Treating that as an empty
        # day would put a confident zero on the glass.
        first = errors[0] if isinstance(errors, list) and errors else errors
        message = (first or {}).get("message") if isinstance(first, dict) else first
        raise ValueError(f"Shopify: {str(message)[:120]}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise ValueError("Shopify no devolvió datos")
    return data
