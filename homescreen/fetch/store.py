"""Where a job's payload lives between fetching it and drawing it.

Thin on purpose. `cache.py` already owns the envelope -- the timestamp, the ok
flag, the failure record, the refusal to serialise a non-finite -- and that
behaviour was earned by things going wrong on a real Pi. This adds only the
naming: one file per JOB rather than one per device, which is what lets five
screens wanting the same data share a single fetch and a single file.
"""

from __future__ import annotations

import re
from pathlib import Path

from homescreen.cache import read_cache, write_cache, write_failure

#: Keys are generated (`providers.key`) but reach this module from stored
#: records, so they are validated rather than trusted: this value becomes a
#: path.
_SAFE_KEY = re.compile(r"^[a-z0-9_]+-[0-9a-f]{6,32}$")


def jobs_dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / "jobs"


def path_for(cache_dir: Path, key: str) -> Path:
    if not _SAFE_KEY.match(str(key)):
        raise ValueError(f"unusable job key: {key!r}")
    return jobs_dir(cache_dir) / f"{key}.json"


def read(cache_dir: Path, key: str) -> dict | None:
    """The envelope, or None if there is nothing usable. Never raises."""
    try:
        return read_cache(path_for(cache_dir, key))
    except (ValueError, OSError):
        return None


def save(cache_dir: Path, key: str, payload: dict) -> None:
    write_cache(path_for(cache_dir, key), payload)


def record_failure(cache_dir: Path, key: str, error: str) -> None:
    """A failed fetch keeps the last good payload and marks it not-ok.

    Blanking on one timeout is how a panel goes empty during a hiccup; the
    device has its own staleness rules and would rather have old data it can
    judge than no data it cannot.
    """
    write_failure(path_for(cache_dir, key), error)


def prune(cache_dir: Path, keep: set) -> int:
    """Delete payloads no job wants any more. Returns how many went.

    Jobs are derived from assignments, so a screen changing what it shows can
    orphan a file. On a microSD that is wear and clutter; more importantly a
    stale file left behind will be read as data by anything that looks it up by
    key later.
    """
    removed = 0
    directory = jobs_dir(cache_dir)
    if not directory.is_dir():
        return 0
    for path in directory.glob("*.json"):
        if path.stem in keep:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
