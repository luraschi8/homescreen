"""What a component gets back when it asks for its data.

A payload alone is not enough and pretending otherwise pushes the same
arithmetic into every component. A component needs to know how OLD what it has
is, because that is the difference between "21 degrees" and "21 degrees, as of
an hour ago" -- and for the radar it is the difference between drawing aircraft
and drawing where aircraft used to be.

So the port returns this rather than a dict. It is never None: "nothing has
been fetched" is a Reading with no data, which every component must handle
anyway, and returning None makes each of them re-invent the check.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Reading:
    """One provider's answer, with its age."""
    data: dict | None = None
    #: Whether the LAST fetch succeeded. False with data present is the normal
    #: and important case: a failed fetch keeps the last good payload, and a
    #: component would rather draw old data it can label than nothing at all.
    ok: bool = False
    age_s: float | None = None
    fetched_at: str | None = None

    @property
    def missing(self) -> bool:
        return not self.data

    def get(self, key, default=None):
        """Read one field of the payload without unwrapping it first."""
        return (self.data or {}).get(key, default)

    @classmethod
    def nothing(cls) -> "Reading":
        return cls()

    @classmethod
    def from_envelope(cls, env, *, now: float) -> "Reading":
        """Read a cache envelope. Never raises: this is on the serve path over
        a file the fetch daemon writes."""
        if not isinstance(env, dict):
            return cls.nothing()
        data = env.get("data")
        age = None
        stamp = env.get("fetched_at")
        if isinstance(stamp, str):
            try:
                from datetime import datetime
                moment = datetime.fromisoformat(stamp)
                if moment.tzinfo is None:
                    # A NAIVE stamp is unknown, not fresh. Interpreting it in
                    # local time makes a dead feed look current, which leaves a
                    # device permanently blind to it -- the failure is silent
                    # and total, and this was a real bug once already.
                    age = None
                else:
                    age = max(0.0, now - moment.timestamp())
            except (TypeError, ValueError):
                age = None
        return cls(data=data if isinstance(data, dict) else None,
                   ok=bool(env.get("ok")), age_s=age,
                   fetched_at=stamp if isinstance(stamp, str) else None)
