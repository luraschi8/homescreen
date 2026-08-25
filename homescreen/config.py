# homescreen/config.py
from __future__ import annotations

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


def feed_cache_path(cache_dir: Path, dev: dict) -> Path:
    """Cache file for this device. Keyed by device id, not by a literal and not
    by feed: `home`, `radius_km` and `max_aircraft` are per-device, so two
    radars with different centres must not share one file."""
    return cache_dir / "feed" / f"{dev['id']}.json"
