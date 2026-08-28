"""Upcoming events from an iCalendar feed.

An ICS URL is a PARAMETER, not a credential: it is per-assignment by nature
(this screen shows that calendar), and the secret-ish part of a Google or
Apple private address is the URL itself. So two screens on two calendars are
two jobs by the ordinary rule, with no credential involved.

Parsed here rather than at draw time, and reduced to the few fields a screen
can show. An ICS file is a large, recursive format and a component should never
see one: the payload that reaches a scene is a list of {when, summary}.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

NAME = "ics"

PARAMS = (
    {"key": "url", "label": "URL del calendario (.ics)", "type": "text"},
    {"key": "days", "label": "Días por delante", "type": "int", "default": 14},
)

#: Calendars change when a person changes them, which is rarely and never
#: urgently. Fifteen minutes keeps a newly-accepted invitation visible within
#: the time it takes to walk to the screen.
DEFAULT_INTERVAL_S = 900

MIN_SPACING_S = 0.5
SECRETS: tuple = ()

TIMEOUT_S = (3.05, 10)

#: An ICS feed is text, and a calendar with a year of history is large. Enough
#: for a busy year, refused past that rather than parsed into memory on a Pi.
MAX_BYTES = 2_000_000
MAX_EVENTS = 50

_LINE = re.compile(r"^(?P<key>[A-Z-]+)(?P<params>;[^:]*)?:(?P<value>.*)$")


def clean_params(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    url = str(raw.get("url") or "").strip()
    if not url:
        raise ValueError("hace falta la URL del calendario")
    if not url.startswith(("http://", "https://", "webcal://")):
        raise ValueError("la URL debe empezar por http://, https:// o webcal://")
    if len(url) > 500:
        raise ValueError("la URL es demasiado larga")
    try:
        days = int(raw.get("days") or 14)
    except (TypeError, ValueError):
        days = 14
    return {"url": url, "days": max(1, min(60, days))}


def fetch(params: dict, *, session=None, secrets=None) -> dict:
    if session is None:
        import requests
        session = requests.Session()
    url = params["url"].replace("webcal://", "https://", 1)
    resp = session.get(url, timeout=TIMEOUT_S)
    resp.raise_for_status()
    text = resp.text
    if len(text) > MAX_BYTES:
        raise ValueError("el calendario es demasiado grande")
    if "BEGIN:VCALENDAR" not in text:
        # A login page is still a 200 and still text. Treating it as an empty
        # calendar would show "nothing coming up" forever, which is a lie a
        # person acts on.
        raise ValueError("la respuesta no es un calendario iCalendar")
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=int(params.get("days", 14)))
    events = [e for e in _events(text) if now - timedelta(hours=12) <= e[0] <= horizon]
    events.sort(key=lambda e: e[0])
    return {"events": [{"when": when.isoformat(), "summary": summary}
                       for when, summary in events[:MAX_EVENTS]]}


def _events(text: str):
    """(start, summary) pairs. Skips anything it cannot read rather than
    failing: one malformed event must not hide a month of good ones."""
    start = summary = None
    for raw in _unfolded(text):
        match = _LINE.match(raw)
        if not match:
            continue
        key, value = match.group("key"), match.group("value")
        if key == "BEGIN" and value == "VEVENT":
            start = summary = None
        elif key == "DTSTART":
            start = _stamp(value, match.group("params") or "")
        elif key == "SUMMARY":
            summary = value.replace("\\,", ",").replace("\\n", " ").strip()
        elif key == "END" and value == "VEVENT":
            if start is not None:
                yield start, (summary or "(sin título)")[:120]
            start = summary = None


def _unfolded(text: str):
    """ICS folds long lines by continuing them with a leading space."""
    current = ""
    for line in text.splitlines():
        if line[:1] in (" ", "\t"):
            current += line[1:]
            continue
        if current:
            yield current
        current = line
    if current:
        yield current


def _stamp(value: str, params: str):
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc)
        if "T" in value:
            # Floating or zoned local time. Without the VTIMEZONE machinery the
            # honest reading is the server's own zone, which is where the
            # screen is.
            naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
            return naive.astimezone()
        # An all-day event: a date, no time.
        return datetime.strptime(value, "%Y%m%d").astimezone()
    except (ValueError, TypeError):
        return None
