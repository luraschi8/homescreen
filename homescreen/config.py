# homescreen/config.py
from __future__ import annotations

import math
from pathlib import Path

import yaml


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path) -> dict:
    """Load config.yaml, then overlay config.local.yaml if present.

    config.yaml is COMMITTED (it is structure: device registry, coordinates,
    cadence). config.local.yaml is gitignored and holds secrets -- SPEC §6 puts
    `quotes.api_key` and the secret `calendar.ics_url` in the config, and this
    repo is public (CLAUDE.md §3/§9). Without this overlay the first person to
    add a key has exactly one obvious place to put it: the committed file.
    """
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} is empty or is not a mapping")
    # NOTE: lists are replaced wholesale, not merged by key. A `devices:` list
    # in the local file therefore REPLACES the registry rather than adding to
    # it. Fine for secrets (scalars under `feeds`); do not put devices there.
    local = path.with_name("config.local.yaml")
    if local.exists():
        with open(local, encoding="utf-8") as fh:
            overlay = yaml.safe_load(fh)
        if isinstance(overlay, dict):
            cfg = _deep_merge(cfg, overlay)
    return cfg


def _whole(dev: dict, label: str, value, lo: float, hi: float) -> int:
    """Range-check a field the consumer will use as an INT, not just a number.

    `_number` validates with float() and `adsb.py` slices with int(), so
    `max_aircraft: "20.5"` passed validation, passed the startup check, and
    then raised inside every fetch forever: "mapping failed: invalid literal
    for int()". One unauthenticated PATCH wedged the feed permanently and the
    operator's own /health reported a Python parse error. Validate the way the
    consumer consumes, or validation is theatre.
    """
    n = _number(dev, label, value, lo, hi)
    if n != int(n):
        raise ValueError(f"device {dev['id']}: {label} must be a whole number, "
                         f"got {value!r}")
    return int(n)


def _flag(dev: dict, label: str, value) -> bool:
    """A boolean field. Unvalidated, `show_ground` accepted 8 MB of text."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in (
            "true", "false", "yes", "no", "1", "0"):
        return value.strip().lower() in ("true", "yes", "1")
    raise ValueError(f"device {dev['id']}: {label} must be true or false, "
                     f"got {value!r}")


def _number(dev: dict, label: str, value, lo: float, hi: float,
            *, inclusive_low: bool = True) -> float:
    """Range-check one numeric config field, or raise ValueError naming it.

    Rejects non-finite as well: `radius_km: .inf` passes any bare `< lo` test
    and then produces a nonsense `dist=inf` upstream request, and inf/nan is
    already rejected everywhere else in this project.
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"device {dev['id']}: {label} must be a number, "
                         f"got {value!r}") from None
    if not math.isfinite(n):
        raise ValueError(f"device {dev['id']}: {label} must be finite, "
                         f"got {value!r}")
    too_low = n < lo if inclusive_low else n <= lo
    if too_low or n > hi:
        bound = f"{lo} to {hi}" if inclusive_low else f"above {lo}, up to {hi}"
        raise ValueError(f"device {dev['id']}: {label} must be {bound}, "
                         f"got {value!r}")
    return n


def check_device(dev: dict) -> None:
    """Validate one device's settable numeric fields, or raise ValueError.

    Lives here rather than with the fetcher so the serve path can validate a
    PATCH without importing the network module -- the C7 guard forbids
    homescreen.sources.adsb ever reaching serve.py's import graph.
    """
    if not isinstance(dev.get("id"), str):
        raise ValueError(f"device entry has no usable id: {dev!r}")
    home = dev.get("home")
    if not isinstance(home, dict):
        raise ValueError(f"device {dev['id']}: home must be a mapping, got {home!r}")
    _number(dev, "home.lat", home.get("lat"), -90.0, 90.0)
    _number(dev, "home.lon", home.get("lon"), -180.0, 180.0)
    # radius_km 0 would fetch nothing forever while the unit stays green --
    # the exact silent failure this validation exists to prevent -- so the
    # lower bound is exclusive. max_aircraft 1 is odd but usable.
    _number(dev, "radius_km", dev.get("radius_km", 60), 0.0, 20000.0,
            inclusive_low=False)
    _whole(dev, "max_aircraft", dev.get("max_aircraft", 20), 1.0, 1000.0)
    _number(dev, "poll_seconds", dev.get("poll_seconds", 5), 1.0, 3600.0)
    # Every settable key must be checked here: overrides.SETTABLE_KEYS listed
    # six and this function validated five, so the unchecked one was a free
    # write channel into a file on two hot paths.
    _flag(dev, "show_ground", dev.get("show_ground", False))


def mapping(value) -> dict:
    """Coerce a config node to a dict.

    `cfg.get("feeds", {})` does NOT protect you: the `{}` default fires only
    when the key is ABSENT. `feeds:` with its children commented out is a
    *present* key whose value is `None`, and `None.get(...)` is an
    AttributeError -- which is neither OSError nor ValueError, so it escapes
    every handler written for config faults and exits 1 into a restart loop.
    Wrong-typed nodes (a list, a string) behave the same way.

    Every nested config read in this project goes through here.
    """
    return value if isinstance(value, dict) else {}


def feed_config(cfg: dict, name: str = "adsb") -> dict:
    return mapping(mapping(cfg.get("feeds")).get(name))


def server_config(cfg: dict) -> dict:
    return mapping(cfg.get("server"))


def device(cfg: dict, device_id: str) -> dict | None:
    devices = cfg.get("devices")
    if not isinstance(devices, list):
        return None          # a non-list here would raise into the serve path
    for d in devices:
        if isinstance(d, dict) and d.get("id") == device_id:
            return d
    return None


def feed_data_path(cache_dir: Path, feed_name: str) -> Path:
    """Cache file for a FEED, not a device.

    Corrected after a device assigned the `planes` scene showed an empty sky
    forever: the fetcher wrote cache/feed/radar.json while the scene looked for
    cache/feed/<device>.json, so any discovered device got silence.

    Keying by device only works if every device carries `home`/`radius_km`,
    and a self-registered device carries neither -- it declares a screen size,
    not a location. The subscription is per-location: two screens in one house
    want the same aircraft. Different centres are a different FEED.

    This supersedes ADDENDUM §7 / PLAN.md A2 / VALIDATION C7, which all name
    cache/feed/radar.json from before devices could self-register.
    """
    return cache_dir / "feed" / f"{str(feed_name or 'adsb')}.json"


def feed_cache_path(cache_dir: Path, dev: dict) -> Path:
    """The feed file a DEVICE reads. Resolves through the device's `feed` key."""
    return feed_data_path(cache_dir, (dev or {}).get("feed") or "adsb")
