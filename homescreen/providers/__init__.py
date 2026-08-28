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

#: Bounds any provider's cadence must respect, whatever it declares or is
#: asked for. The low end protects an upstream we do not own; the high end
#: keeps a stalled job visibly stalled rather than merely quiet.
MIN_INTERVAL_S = 5
MAX_INTERVAL_S = 6 * 3600


def _modules() -> dict:
    from homescreen.providers import adsb, openweather
    return {m.NAME: m for m in (adsb, openweather)}


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
    raw = getattr(provider, "DEFAULT_INTERVAL_S", 300) if provider else 300
    return clamp_interval(raw)


def clamp_interval(seconds) -> int:
    try:
        n = int(float(seconds))
    except (TypeError, ValueError):
        return 300
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
