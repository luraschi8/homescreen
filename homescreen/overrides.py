"""Runtime device overrides, settable over HTTP and shared between daemons.

The fetch daemon and the serve daemon are separate processes, each reading
config.yaml once at startup. A PATCH handled by serve.py therefore cannot
reach the fetcher in memory -- and the fetcher is the process that actually
uses max_aircraft and radius_km. So overrides go through a file that both
re-read: serve.py per request, the fetch loop once per cycle (it is already
waking every few seconds, so this costs one small read).

Only SETTABLE_KEYS may be overridden. Structural fields -- id, kind, render,
feed -- are deliberately not settable: changing them at runtime would rename
cache files and re-route endpoints under a live device.
"""

from __future__ import annotations

import json
import os
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Dotted paths, resolved against a device entry.
SETTABLE_KEYS = ("max_aircraft", "radius_km", "poll_seconds", "show_ground",
                 "home.lat", "home.lon")

#: Reserved top-level key holding FEED settings rather than a device's.
#:
#: The sigil is deliberate: this file is keyed by device id, and a bare "feeds"
#: could collide with a device someone actually named that. Nothing in
#: config.yaml gives a device an id starting with "@".
FEEDS_KEY = "@feeds"

#: What an operator may change about a feed from the dashboard.
#:
#: Not the whole feed block: `source` selects which fetcher module runs and
#: `api_key` is a secret that must never be rendered back (CLAUDE.md §7.4), so
#: neither is settable from an unauthenticated page.
FEED_SETTABLE_KEYS = ("endpoint", "fetch_seconds")

#: Bounds. The endpoint is fetched by a daemon on a loop, so a value that is
#: not a URL is a tight failure loop rather than a typo.
MAX_ENDPOINT_LEN = 500
FETCH_SECONDS_RANGE = (1.0, 3600.0)


def overrides_path(cache_dir: Path) -> Path:
    return cache_dir / "overrides.json"


def load(cache_dir: Path) -> dict:
    """{device_id: {dotted_key: value}}. Never raises.

    A corrupt or hand-edited file degrades to "no overrides" rather than
    stopping either daemon -- this file is written from the network, so a bad
    one must never be able to wedge the service.
    """
    try:
        with open(overrides_path(cache_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, dict)}


def _fsync_dir(path: Path) -> None:
    """A rename is only durable once the DIRECTORY entry is synced."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def save(cache_dir: Path, data: dict) -> None:
    """Atomic, so a reader never sees a half-written overlay."""
    path = overrides_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, allow_nan=False)
            # registry.py and cache.py both fsync here and this did not, though
            # it is written from the same unauthenticated network and re-read
            # by the fetch daemon. rename-without-fsync on ext4 after an
            # unclean power cut (mains Pi, microSD, no UPS) classically yields
            # a zero-length file: `load` degrades to {} and every override the
            # operator set silently reverts.
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)
    _fsync_dir(path.parent)


def _set_dotted(target: dict, dotted: str, value) -> None:
    head, _, tail = dotted.partition(".")
    if not tail:
        target[head] = value
        return
    node = target.get(head)
    target[head] = dict(node) if isinstance(node, dict) else {}
    _set_dotted(target[head], tail, value)


def apply(cfg: dict, cache_dir: Path, *, _data: dict | None = None) -> dict:
    """cfg with the runtime overlay applied. Never mutates cfg. Never raises.

    `_data` lets a caller probe a candidate overlay without persisting it --
    used to validate a PATCH before anything reaches disk.
    """
    data = load(cache_dir) if _data is None else _data
    if not data:
        return cfg
    devices = cfg.get("devices")
    if not isinstance(devices, list):
        return cfg
    merged = []
    for dev in devices:
        if not isinstance(dev, dict):
            # Kept, not dropped. Skipping it silently shortened the device
            # list, so a single bad entry in a hand-edited config.yaml would
            # make /api/status report fewer devices than the file declares --
            # a config fault presenting as a missing device.
            log.warning("config device entry is not a mapping: %r", dev)
            merged.append(dev)
            continue
        over = data.get(dev.get("id"))
        if not over:
            merged.append(dev)
            continue
        copy = dict(dev)
        for dotted, value in over.items():
            if dotted in SETTABLE_KEYS:
                _set_dotted(copy, dotted, value)
            else:
                log.warning("ignoring non-settable override %r for %s",
                            dotted, dev.get("id"))
        merged.append(copy)
    out = dict(cfg)
    out["devices"] = merged
    feeds = data.get(FEEDS_KEY)
    if isinstance(feeds, dict) and isinstance(cfg.get("feeds"), dict):
        # The fetch daemon re-reads this every cycle, so an endpoint changed on
        # the dashboard reaches it without a restart -- the same route
        # max_aircraft already takes.
        merged_feeds = {}
        for name, block in cfg["feeds"].items():
            over = feeds.get(name)
            if isinstance(block, dict) and isinstance(over, dict):
                block = {**block, **{k: v for k, v in over.items()
                                     if k in FEED_SETTABLE_KEYS}}
            merged_feeds[name] = block
        out["feeds"] = merged_feeds
    return out


def clean_feed_settings(raw: dict) -> dict:
    """Coerce a dashboard form into storable feed settings, or raise ValueError.

    Raises rather than dropping: this one is a human pressing Save and waiting
    to be told whether it worked. Silently ignoring a typo would leave them
    staring at a value the daemon is not using.
    """
    out = {}
    endpoint = raw.get("endpoint")
    if endpoint is not None:
        endpoint = str(endpoint).strip()
        if not endpoint:
            raise ValueError("el endpoint no puede estar vacío")
        if len(endpoint) > MAX_ENDPOINT_LEN:
            raise ValueError(f"el endpoint supera {MAX_ENDPOINT_LEN} caracteres")
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("el endpoint debe empezar por http:// o https://")
        out["endpoint"] = endpoint
    every = raw.get("fetch_seconds")
    if every is not None and str(every).strip() != "":
        try:
            n = float(every)
        except (TypeError, ValueError):
            raise ValueError("la cadencia debe ser un número") from None
        low, high = FETCH_SECONDS_RANGE
        if not low <= n <= high:
            raise ValueError(f"la cadencia debe estar entre {int(low)} y "
                             f"{int(high)} segundos")
        out["fetch_seconds"] = int(n) if n.is_integer() else n
    return out


def set_feed(cache_dir: Path, name: str, values: dict) -> dict:
    """Persist feed settings. Raises ValueError on anything unusable."""
    cleaned = clean_feed_settings(values)
    if not cleaned:
        return {}
    data = load(cache_dir)
    feeds = dict(data.get(FEEDS_KEY) or {})
    feeds[str(name)] = {**(feeds.get(str(name)) or {}), **cleaned}
    data[FEEDS_KEY] = feeds
    save(cache_dir, data)
    return cleaned
