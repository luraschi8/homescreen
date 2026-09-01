"""NBA fixtures, from the league's own schedule feed.

Keyless: no account, no quota, nothing to leak through an error string onto an
unauthenticated LAN page. Chosen over a keyed vendor whose free plan carries
an undocumented season restriction -- one that does not degrade, it collapses,
and the docs give no way to know in advance.

THE HEADER SET IS LOAD-BEARING. `cdn.nba.com` answers 403 without it, and the
failure looks exactly like an outage. Verified 2026-09-01: a request with a
User-Agent, gzip, the `Sec-Fetch-*` trio, `Accept-Language` and an nba.com
referer returns 192 KB; the same request with any of the User-Agent or the
encoding removed returns 403 and 431 bytes. `tests/test_nba.py` asserts the
headers, so tidying them away fails a test rather than producing an outage
nobody can explain.

Undocumented, so the shape can change without notice. `runner` keeps the last
good envelope and sets `ok: false`, which makes that a stale tile rather than
a blank panel -- and the fixture in the tests is the only specification this
feed will ever have, so it was recorded from a real response.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homescreen.fetch.providers import _fixtures

NAME = "nba"
ENDPOINT = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
TIMEOUT_S = 20

#: Every one of these is required. See the module docstring.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/140.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

PARAMS = (
    {"key": "team", "label": "Equipo", "type": "text", "default": "",
     "help": "Abreviatura: LAL, BOS, GSW. En blanco trae toda la liga."},
    {"key": "days", "label": "Días por delante", "type": "int", "default": 30},
)

#: The whole league's season in one 190 KB document. Twice a day is plenty
#: for a schedule that changes when a game is postponed, and it keeps a
#: 190 KB download off an SD card CLAUDE.md flags for wear.
DEFAULT_INTERVAL_S = 43200

SECRETS = ()

MAX_MATCHES = 40


def clean_params(raw: dict) -> dict:
    team = str((raw or {}).get("team") or "").strip().upper()[:3]
    if team and not team.isalpha():
        raise ValueError("la abreviatura del equipo son letras: LAL, BOS")
    try:
        days = int((raw or {}).get("days") or 30)
    except (TypeError, ValueError):
        days = 30
    return {"team": team, "days": max(1, min(120, days))}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    if session is None:
        import requests
        session = requests.Session()
    resp = session.get(ENDPOINT, timeout=TIMEOUT_S, headers=HEADERS)
    resp.raise_for_status()
    body = resp.json()
    days = (((body or {}).get("leagueSchedule") or {}).get("gameDates"))
    if not isinstance(days, list):
        raise ValueError("respuesta inesperada de cdn.nba.com")

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=int(params.get("days", 30)))
    want = str(params.get("team") or "")
    out = []
    for day in days:
        for game in (day or {}).get("games") or ():
            if not isinstance(game, dict):
                continue
            when = str(game.get("gameDateTimeUTC") or "")
            moment = _instant(when)
            if moment is None or not (now - timedelta(days=1) <= moment <= horizon):
                continue
            home = _side(game.get("homeTeam"))
            away = _side(game.get("awayTeam"))
            if not home or not away:
                continue
            if want and want not in (_code(game.get("homeTeam")),
                                     _code(game.get("awayTeam"))):
                continue
            out.append(_fixtures.match(
                when.replace("Z", "+00:00"), home, away,
                status=_status(game.get("gameStatus")),
                competition="NBA",
                home_score=(game.get("homeTeam") or {}).get("score"),
                away_score=(game.get("awayTeam") or {}).get("score")))
            if len(out) >= MAX_MATCHES:
                break
    out.sort(key=lambda m: m["when"])
    return {"matches": out}


def _instant(raw: str):
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _side(team) -> str:
    team = team if isinstance(team, dict) else {}
    return str(team.get("teamTricode") or team.get("teamName") or "").strip()


def _code(team) -> str:
    return str((team if isinstance(team, dict) else {}).get("teamTricode") or "")


def _status(value) -> str:
    """1 scheduled, 2 in play, 3 final -- the feed's own numbering."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return _fixtures.SCHEDULED
    if code == 2:
        return _fixtures.LIVE
    if code == 3:
        return _fixtures.FINISHED
    return _fixtures.SCHEDULED
