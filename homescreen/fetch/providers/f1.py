"""Formula 1, from jolpica.

Keyless. Ergast, which every F1 tool used to read, was retired -- its domain
now serves gambling spam -- and jolpica is the community continuation with the
same response shape.

A race has no home and away side, so it is modelled as the event name against
its country: "Italian Grand Prix" at "Italy" reads correctly in the same row
as "Real Madrid — Barcelona" without the component needing to know which
sport it is looking at.
"""

from __future__ import annotations

from homescreen.fetch.providers import _fixtures

NAME = "f1"
ENDPOINT = "https://api.jolpi.ca/ergast/f1/{season}/{scope}.json"
TIMEOUT_S = 10

PARAMS = (
    {"key": "season", "label": "Temporada", "type": "text",
     "default": "current"},
)

#: A race weekend moves on the scale of days. Hourly is generous.
DEFAULT_INTERVAL_S = 3600

SECRETS = ()

MAX_RACES = 12


def clean_params(raw: dict) -> dict:
    season = str((raw or {}).get("season") or "current").strip().lower()
    if season != "current" and not (season.isdigit() and len(season) == 4):
        raise ValueError("la temporada debe ser «current» o un año de 4 cifras")
    return {"season": season}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    if session is None:
        import requests
        session = requests.Session()
    resp = session.get(
        ENDPOINT.format(season=params.get("season", "current"), scope="races"),
        timeout=TIMEOUT_S)
    resp.raise_for_status()
    body = resp.json()
    races = (((body or {}).get("MRData") or {}).get("RaceTable") or {}).get("Races")
    if not isinstance(races, list):
        raise ValueError("respuesta inesperada de jolpica")
    out = []
    for race in races[:MAX_RACES]:
        if not isinstance(race, dict):
            continue
        date, clock = race.get("date"), race.get("time")
        if not date:
            continue
        # jolpica gives "13:00:00Z"; without the offset the start would be
        # read in the server's zone.
        when = f"{date}T{clock}" if clock else f"{date}T00:00:00Z"
        out.append(_fixtures.match(
            when.replace("Z", "+00:00"),
            str(race.get("raceName") or ""),
            str(((race.get("Circuit") or {}).get("Location") or {})
                .get("country") or ""),
            competition="F1"))
    out.sort(key=lambda m: m["when"])
    return {"matches": out}
