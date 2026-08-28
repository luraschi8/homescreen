"""Fixtures and results for one team, from football-data.org.

Named for what it fetches rather than for the vendor, because the component
asks for "this team's next match" and should not have to change if the source
does. The free tier covers the major European competitions, which is the whole
requirement here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

NAME = "football"

PARAMS = (
    {"key": "team", "label": "ID del equipo", "type": "int"},
    {"key": "days", "label": "Días por delante", "type": "int", "default": 30},
)

#: Fixtures move on the scale of days and results settle within minutes of
#: full time. Half an hour is well inside both, and the free tier is 10 calls a
#: minute -- which this could not exhaust with a screen per room.
DEFAULT_INTERVAL_S = 1800
MIN_SPACING_S = 1.0

SECRETS = ("api_key",)

ENDPOINT = "https://api.football-data.org/v4/teams/{team}/matches"
TIMEOUT_S = (3.05, 10)

MAX_MATCHES = 10


def clean_params(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    try:
        team = int(raw["team"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("hace falta el ID del equipo") from None
    if not 1 <= team <= 99_999:
        raise ValueError("ID de equipo fuera de rango")
    try:
        days = int(raw.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    return {"team": team, "days": max(1, min(120, days))}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    key = (secrets or {}).get("api_key")
    if not key:
        raise ValueError("falta la clave de football-data.org")
    if session is None:
        import requests
        session = requests.Session()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=int(params.get("days", 30)))
    resp = session.get(
        ENDPOINT.format(team=params["team"]), timeout=TIMEOUT_S,
        headers={"X-Auth-Token": key},
        params={"dateFrom": (now - timedelta(days=3)).date().isoformat(),
                "dateTo": horizon.date().isoformat()})
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict) or not isinstance(body.get("matches"), list):
        raise ValueError("respuesta inesperada de football-data.org")
    matches = []
    for raw in body["matches"][:MAX_MATCHES]:
        if not isinstance(raw, dict):
            continue
        home = _side(raw.get("homeTeam"))
        away = _side(raw.get("awayTeam"))
        if not home or not away:
            continue
        score = raw.get("score") or {}
        full = score.get("fullTime") or {}
        matches.append({
            "when": str(raw.get("utcDate") or ""),
            "home": home, "away": away,
            "status": str(raw.get("status") or ""),
            "home_goals": _int(full.get("home")),
            "away_goals": _int(full.get("away")),
            "competition": _side(raw.get("competition")),
        })
    matches.sort(key=lambda m: m["when"])
    return {"team": params["team"], "matches": matches}


def _side(value) -> str:
    if not isinstance(value, dict):
        return ""
    # `shortName` is what fits a panel; `name` is the legal one and is long.
    return str(value.get("shortName") or value.get("name") or "").strip()[:24]


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
