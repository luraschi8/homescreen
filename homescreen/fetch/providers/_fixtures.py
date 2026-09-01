"""The shape every sport source normalises into.

Same argument as the weather envelope: a component that reads
`homeTeam.shortName` has learned it is talking to football-data.org, and the
source cannot then be changed without editing the component. The adapters
translate; the component reads only this.

Deliberately thin. A fixture is two names, a time, and how far along it is --
which is all a panel across a room can say about it, whatever the sport.
"""

from __future__ import annotations

#: How far along a fixture is, normalised. Every vendor has its own spelling
#: and its own extra states; these are the three a panel can act on.
SCHEDULED, LIVE, FINISHED = "SCHEDULED", "LIVE", "FINISHED"

#: What a component may assume is present on every entry.
REQUIRED = ("when", "home", "away", "status", "competition")


def match(when: str, home: str, away: str, *, status: str = SCHEDULED,
          competition: str = "", home_score=None, away_score=None) -> dict:
    """One fixture.

    `when` is an ISO 8601 instant WITH an offset. A naive timestamp would be
    read in the server's zone, which is right in Madrid and wrong for a race
    in Melbourne.
    """
    return {"when": str(when or ""), "home": str(home or ""),
            "away": str(away or ""),
            "status": status if status in (SCHEDULED, LIVE, FINISHED)
            else SCHEDULED,
            "competition": str(competition or ""),
            "home_score": _score(home_score), "away_score": _score(away_score)}


def _score(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
