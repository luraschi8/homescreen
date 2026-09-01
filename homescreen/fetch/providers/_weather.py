"""The shape every weather source normalises into.

A component that decodes `01d` has learned which vendor it is talking to, and
the source cannot then be changed without editing the component. So the
adapters translate and the component reads only this: "configurable source"
becomes a dropdown rather than a rewrite.

Absent is a legitimate answer. A current-conditions endpoint has no forecast,
and the honest envelope says so by leaving `daily` out rather than shipping an
empty list that reads as "no weather tomorrow".
"""

from __future__ import annotations

#: Every sky this system can draw. Deliberately short: the panel is 1-bit and
#: the icons are line drawings, so "few clouds" and "broken clouds" are the
#: same picture. A vendor distinction that cannot reach the glass is noise.
SKY = ("clear", "cloud", "rain", "snow", "storm", "fog")

#: What a component may assume is always there.
REQUIRED = ("temp", "description", "place", "sky", "units",
            "sunrise", "sunset", "tz_offset_s")

#: Optional, and preferred where present: a zone knows about transitions and a
#: fixed offset does not.
TZ = "tz"


def day(date, low, high, sky: str, precip_pct=None) -> dict:
    """One entry of a daily forecast. `date` is an epoch, kept as a NUMBER.

    Stringifying it was a real bug: the component formats it with the place's
    own offset, and `"1788213600"` is not something you can add hours to.
    """
    return {"date": epoch(date), "min": low, "max": high,
            "sky": sky if sky in SKY else "cloud",
            "precip_pct": precip_pct}


def hour(time, temp, sky: str) -> dict:
    """One entry of an hourly strip. `time` is an epoch, kept as a NUMBER."""
    return {"time": epoch(time), "temp": temp,
            "sky": sky if sky in SKY else "cloud"}


def num(value):
    """A finite float, or None. Never raises."""
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def epoch(value):
    """A whole number of seconds, or None.

    Separate from `num` because a timestamp of 0.5 is not a timestamp, and a
    bool is an int in Python -- `sunrise: true` would otherwise become 1970.
    """
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
