"""EuroLeague and EuroCup fixtures, from the league's own feed.

Keyless. Deferred once because the documented `/v1/schedules` endpoint answers
in XML, and parsing untrusted XML with the standard library exposes entity
expansion on a box with no `defusedxml`. `/v2/.../games` answers the same data
as JSON, which removes the question rather than mitigating it -- worth the
five minutes it took to look.

The season's whole schedule is one document, so it is fetched rarely and only
the normalised subset is stored. A microSD that CLAUDE.md flags for wear does
not want a megabyte rewritten every ten minutes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homescreen.fetch.providers import _fixtures

NAME = "euroleague"
ENDPOINT = ("https://api-live.euroleague.net/v2/competitions/{competition}"
            "/seasons/{season}/games")
TIMEOUT_S = 25

PARAMS = (
    {"key": "competition", "label": "Competición", "type": "choice",
     "choices": ("E", "U"), "default": "E",
     "help": "E = EuroLeague, U = EuroCup."},
    {"key": "team", "label": "Equipo", "type": "text", "default": "",
     "help": "Código: MAD, BAR, OLY, PAN. En blanco trae toda la competición."},
    {"key": "days", "label": "Días por delante", "type": "int", "default": 30},
)

#: A season's schedule changes when a game moves, which is not often. The
#: document is around a megabyte, so this is deliberately infrequent.
DEFAULT_INTERVAL_S = 43200

SECRETS = ()

MAX_MATCHES = 40


def clean_params(raw: dict) -> dict:
    competition = str((raw or {}).get("competition") or "E").strip().upper()[:1]
    if competition not in ("E", "U"):
        raise ValueError("la competición debe ser E (EuroLeague) o U (EuroCup)")
    team = str((raw or {}).get("team") or "").strip().upper()[:4]
    if team and not team.isalpha():
        raise ValueError("el código del equipo son letras: MAD, BAR, OLY")
    try:
        days = int((raw or {}).get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    season = str((raw or {}).get("season") or "").strip().upper()
    return {"competition": competition, "team": team,
            "days": max(1, min(120, days)),
            "season": season or _season(competition)}


def _season(competition: str, now=None) -> str:
    """The season code for today: E2025 runs autumn 2025 to spring 2026.

    Derived rather than configured, because a hardcoded season silently stops
    returning fixtures one summer and looks exactly like the feed going away.
    """
    now = now or datetime.now(timezone.utc)
    year = now.year if now.month >= 8 else now.year - 1
    return f"{competition}{year}"


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    if session is None:
        import requests
        session = requests.Session()
    resp = session.get(
        ENDPOINT.format(competition=params.get("competition", "E"),
                        season=params.get("season", "E2025")),
        timeout=TIMEOUT_S, headers={"Accept": "application/json"})
    resp.raise_for_status()
    body = resp.json()
    games = (body or {}).get("data")
    if not isinstance(games, list):
        raise ValueError("respuesta inesperada de euroleague.net")

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=int(params.get("days", 30)))
    want = str(params.get("team") or "")
    label = "EuroLeague" if params.get("competition") == "E" else "EuroCup"
    out = []
    for game in games:
        if not isinstance(game, dict):
            continue
        moment = _instant(game.get("utcDate"))
        if moment is None or not (now - timedelta(days=2) <= moment <= horizon):
            continue
        home, home_code = _side(game.get("local"))
        away, away_code = _side(game.get("road"))
        if not home or not away:
            continue
        if want and want not in (home_code, away_code):
            continue
        out.append(_fixtures.match(
            str(game.get("utcDate") or "").replace("Z", "+00:00"),
            home, away,
            status=(_fixtures.FINISHED if game.get("played")
                    else _fixtures.SCHEDULED),
            competition=label,
            home_score=(game.get("local") or {}).get("score"),
            away_score=(game.get("road") or {}).get("score")))
        if len(out) >= MAX_MATCHES:
            break
    out.sort(key=lambda m: m["when"])
    return {"matches": out}


def _instant(raw):
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _side(entry) -> tuple:
    club = ((entry if isinstance(entry, dict) else {}).get("club")) or {}
    name = str(club.get("abbreviatedName") or club.get("name") or "").strip()
    return name, str(club.get("code") or "").strip().upper()
