"""Providers: the things that know how to fetch one kind of data.

A provider is an ADAPTER behind a narrow port. It declares what parameters it
takes, how often it is worth asking, and how to turn parameters into a payload.
It knows nothing about screens, components, schedules or devices -- and nothing
about screens knows how it works.

The port is deliberately small:

    NAME                 str
    PARAMS               a schema, same vocabulary as a component's options
    DEFAULT_INTERVAL_S   how often this data is worth re-fetching
    SECRETS              names of credentials it needs, if any
    clean_params(raw)    coerce; raise ValueError if unusable
    fetch(params, ...)   -> payload dict

Everything above this line is data. `fetch` is the only function that touches
the network, which is what lets job collection, deduplication and scheduling be
tested without one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class ProviderPort(Protocol):
    """What every adapter must be.

    Written down because `getattr` defaults made the port unfalsifiable: an
    object with only `fetch` satisfied every accessor here, and an adapter that
    forgot `clean_params` got NO validation while `clean_params`'s own
    docstring promised it raises on unusable input. A contract test walks the
    registry against this, so a provider that does not implement the port fails
    at registration rather than at three in the morning.
    """
    NAME: str
    PARAMS: tuple
    SECRETS: tuple
    DEFAULT_INTERVAL_S: int

    def clean_params(self, raw: dict) -> dict: ...

    def fetch(self, params: dict, *, session=None, secrets=None) -> dict: ...


#: A provider's name becomes part of a job key, and a job key becomes a
#: filename. Constrained here so `key()` cannot mint something the store
#: refuses -- the two used to agree only by luck.
NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")

#: Used when a provider declares no cadence of its own.
FALLBACK_INTERVAL_S = 300

#: Bounds any provider's cadence must respect, whatever it declares or is
#: asked for. The low end protects an upstream we do not own; the high end
#: keeps a stalled job visibly stalled rather than merely quiet.
MIN_INTERVAL_S = 5
MAX_INTERVAL_S = 6 * 3600


def min_spacing(name: str) -> float:
    """Seconds the runner must leave between two requests to this provider.

    A per-JOB cadence says nothing about N jobs' spacing, and that is the
    constraint that actually exists: adsb.fi permits one request a second, so
    five radars on five centres firing together violates it however politely
    each one is scheduled. The adapter declares what its upstream permits,
    because the runner cannot invent it.
    """
    provider = get(name)
    try:
        return max(0.0, float(getattr(provider, "MIN_SPACING_S", 0.0)))
    except (TypeError, ValueError):
        return 0.0


#: Memoised: this was rebuilt, with imports, on every call -- once per job per
#: cycle -- and `get()` claimed never to raise while an ImportError escaped it.
_CACHE: dict = {}


def _modules() -> dict:
    if not _CACHE:
        try:
            from homescreen.fetch.providers import (adsb, claude_usage,
                                                   euroleague, f1, football,
                                                   fx, ics, nba, openmeteo,
                                                   openweather, quotes)
            for module in (adsb, claude_usage, euroleague, f1, football, fx,
                           nba, ics, openmeteo, openweather, quotes):
                if not NAME_RE.match(getattr(module, "NAME", "")):
                    log.error("provider %r has an unusable NAME; not registered",
                              getattr(module, "NAME", None))
                    continue
                _CACHE[module.NAME] = module
        except Exception:                               # noqa: BLE001
            # A broken adapter must not take down the daemon or the serve path
            # for every other provider, but it must be loud: silence here is a
            # fleet that quietly stops fetching.
            log.exception("a provider module failed to import")
    return _CACHE


def names() -> tuple[str, ...]:
    return tuple(sorted(_modules()))


def get(name: str):
    """The provider module, or None. Never raises on an unknown name: this is
    reached from stored records and from an unauthenticated page."""
    return _modules().get(str(name))


def params_schema(name: str) -> tuple:
    provider = get(name)
    return tuple(getattr(provider, "PARAMS", ()) or ()) if provider else ()


def secrets_for(name: str) -> tuple[str, ...]:
    provider = get(name)
    return tuple(getattr(provider, "SECRETS", ()) or ()) if provider else ()


def default_interval(name: str) -> int:
    provider = get(name)
    raw = (getattr(provider, "DEFAULT_INTERVAL_S", FALLBACK_INTERVAL_S)
           if provider else FALLBACK_INTERVAL_S)
    return clamp_interval(raw)


def clamp_interval(seconds) -> int:
    try:
        n = int(float(seconds))
    except (TypeError, ValueError):
        return FALLBACK_INTERVAL_S
    return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, n))


def clean_params(name: str, raw) -> dict:
    """Coerce parameters for this provider. Raises ValueError if unusable.

    Raises rather than dropping, unlike most coercion here: a job with silently
    wrong parameters fetches the wrong thing forever and looks healthy doing
    it. The caller decides whether that is a 400 or a skipped requirement.
    """
    provider = get(name)
    if provider is None:
        raise ValueError(f"proveedor desconocido: {name}")
    raw = dict(raw or {})
    # Which credential to use is the RUNNER's business, not the adapter's. It
    # rides in params because it is part of the job's identity -- two screens
    # on two accounts are two fetches -- but no adapter should have to know
    # that, so it is carried around the adapter's own validation.
    scope = raw.pop("secret_scope", None)
    cleaner = getattr(provider, "clean_params", None)
    out = dict(cleaner(raw) if cleaner else raw)
    if scope:
        out["secret_scope"] = str(scope)
    return out


def key(name: str, params: dict) -> str:
    """The identity of a fetch.

    Two screens wanting the same data share one job; two wanting different
    parameters are two jobs. That is the whole reason a job is keyed by its
    PARAMETERS rather than by which screen asked -- five screens showing Madrid
    weather is one request, not five, and nothing upstream has to know how many
    panels are in the house.
    """
    canonical = json.dumps(params or {}, sort_keys=True, separators=(",", ":"),
                           default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return f"{name}-{digest}"
