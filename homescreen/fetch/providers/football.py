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
    {"key": "team", "label": "ID del equipo", "type": "int", "default": 0},
    {"key": "competition", "label": "Competición", "type": "text",
     "default": "",
     "help": "Código: CL, PD, PL, SA, BL1, FL1, CLI. En lugar del equipo."},
    {"key": "days", "label": "Días por delante", "type": "int", "default": 30},
)

#: Fixtures move on the scale of days and results settle within minutes of
#: full time. Half an hour is well inside both, and the free tier is 10 calls a
#: minute -- which this could not exhaust with a screen per room.
DEFAULT_INTERVAL_S = 1800
MIN_SPACING_S = 1.0

SECRETS = ("api_key",)

ENDPOINT = "https://api.football-data.org/v4/teams/{team}/matches"
#: A whole competition rather than one club: "every Champions League tie",
#: which is what you want from a tournament you follow but have no team in.
COMPETITION_ENDPOINT = ("https://api.football-data.org/v4/competitions"
                        "/{competition}/matches")
TIMEOUT_S = (3.05, 10)

MAX_MATCHES = 10


def clean_params(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    competition = str(raw.get("competition") or "").strip().upper()
    if competition:
        # Refused rather than truncated. Codes are 2-5 characters (CL, PD,
        # BL1, CLI), and quietly cutting a longer one to five produces a 404
        # against a code nobody typed.
        if not competition.isalnum() or not 2 <= len(competition) <= 5:
            raise ValueError("el código de competición son 2-5 letras o "
                             "cifras: CL, PD, PL, BL1")
        try:
            days = int(raw.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        return {"team": 0, "competition": competition,
                "days": max(1, min(120, days))}
    try:
        team = int(raw["team"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("hace falta un equipo o una competición") from None
    if not 1 <= team <= 99_999:
        raise ValueError("ID de equipo fuera de rango")
    try:
        days = int(raw.get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    return {"team": team, "competition": "", "days": max(1, min(120, days))}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    key = (secrets or {}).get("api_key")
    if not key:
        raise ValueError("falta la clave de football-data.org")
    if session is None:
        import requests
        session = requests.Session()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=int(params.get("days", 30)))
    competition = str(params.get("competition") or "")
    url = (COMPETITION_ENDPOINT.format(competition=competition) if competition
           else ENDPOINT.format(team=params["team"]))
    resp = session.get(
        url, timeout=TIMEOUT_S,
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
    return {"team": params.get("team", 0),
            "competition": competition, "matches": matches}


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
