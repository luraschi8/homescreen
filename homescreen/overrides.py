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
    return out
