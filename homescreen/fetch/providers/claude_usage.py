"""Token spend from the Anthropic Admin API.

UNVERIFIED SHAPE. I have not run this against a real organisation, and the
usage report's response has not been checked against current documentation --
so the parsing below is deliberately defensive and reports what it could not
read rather than guessing. If the shape is wrong you will see "no se pudo leer
el uso" on the glass and the reason in /api/jobs, which is the failure mode to
want: a wrong number here looks exactly like a right one.

It needs an ADMIN key, which is a different and more powerful credential than a
normal API key, and it reports the ORGANISATION's usage rather than one
person's. Both are stated on the settings page next to the field.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

NAME = "claude_usage"

PARAMS = (
    {"key": "days", "label": "Días", "type": "int", "default": 30},
)

#: Spend accrues over hours and the report is a rollup. Fifteen minutes is far
#: more often than the number meaningfully changes.
DEFAULT_INTERVAL_S = 900
MIN_SPACING_S = 1.0

SECRETS = ("admin_key",)

ENDPOINT = "https://api.anthropic.com/v1/organizations/usage_report/messages"
API_VERSION = "2023-06-01"
TIMEOUT_S = (3.05, 15)


def clean_params(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    try:
        days = int(raw.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    return {"days": max(1, min(90, days))}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    key = (secrets or {}).get("admin_key")
    if not key:
        raise ValueError("falta la clave de administración de Anthropic")
    if session is None:
        import requests
        session = requests.Session()
    days = int(params.get("days", 30))
    start = datetime.now(timezone.utc) - timedelta(days=days)
    resp = session.get(
        ENDPOINT, timeout=TIMEOUT_S,
        headers={"x-api-key": key, "anthropic-version": API_VERSION},
        params={"starting_at": start.replace(microsecond=0).isoformat(),
                "bucket_width": "1d"})
    resp.raise_for_status()
    body = resp.json()
    totals = _totals(body)
    if totals is None:
        raise ValueError("no se pudo leer el uso: formato inesperado")
    totals["days"] = days
    return totals


def _totals(body):
    """Sum whatever token counts the report carries, or None.

    Walks the structure instead of indexing a documented path, because the path
    is the part I could not verify. It sums any `input_tokens`/`output_tokens`
    it finds at any depth, which is right for a bucketed report and returns
    None -- not zero -- when it finds none at all. Zero is a number somebody
    would believe.
    """
    found = {"input_tokens": 0, "output_tokens": 0}
    hits = 0

    def walk(node):
        nonlocal hits
        if isinstance(node, dict):
            for key, value in node.items():
                if key in found and isinstance(value, (int, float)):
                    found[key] += int(value)
                    hits += 1
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node[:5000]:
                walk(item)

    walk(body)
    if not hits:
        return None
    found["total_tokens"] = found["input_tokens"] + found["output_tokens"]
    return found
