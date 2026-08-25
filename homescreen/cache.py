# homescreen/cache.py
"""Cache envelope shared by every fetcher. SPEC §7 fixes this shape."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    """Timezone-aware ISO8601. Aware is load-bearing: a naive stamp is silently
    reinterpreted as local time on parse, shifting every age by the UTC offset."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def _reject_constant(name: str):
    """json.load's default parse_constant happily returns inf/nan. Refuse."""
    raise ValueError(f"non-finite {name} is not strict JSON")


def _finite_float(text: str) -> float:
    """parse_constant only fires for the literals `Infinity`/`NaN`. `1e400` is
    ordinary JSON that float() turns into inf, so it needs its own hook."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"non-finite number {text!r} is not strict JSON")
    return value


def read_cache(path: Path) -> dict | None:
    """Return the envelope, or None if it is absent, corrupt or malformed.

    Never raises. Validates the full SPEC §7 shape, because everything
    downstream indexes these keys unguarded and a 500 in the serve path
    violates SPEC §11.1.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            # parse_constant catches bare Infinity/NaN, which are not strict
            # JSON and which the firmware's parser rejects for the WHOLE body.
            # Refusing here degrades to "no data" (SPEC §11.1); a strict JSON
            # provider on the Flask side would instead make jsonify raise,
            # which is the thing that must never happen in the serve path.
            env = json.load(fh, parse_constant=_reject_constant,
                            parse_float=_finite_float)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(env, dict):
        return None
    if not isinstance(env.get("fetched_at"), str):
        return None
    if not isinstance(env.get("ok"), bool):
        return None
    if not isinstance(env.get("data"), dict):
        return None
    return env


def _write(path: Path, env: dict) -> None:
    """Write atomically so a reader never sees a half-written envelope."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            # allow_nan=False: bare Infinity/NaN is not strict JSON and the
            # firmware's parser rejects the whole body. Belt-and-braces with
            # adsb_map._num and read_cache's parse_constant.
            json.dump(env, fh, separators=(",", ":"), allow_nan=False)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            tmp.unlink(missing_ok=True)   # a refused write leaves nothing behind
        except OSError:
            pass                          # never mask the exception that got us here
        raise
    os.replace(tmp, path)


def write_cache(path: Path, data: dict, *, ok: bool = True,
                error: str | None = None) -> None:
    _write(path, {"fetched_at": _now_iso(), "ok": ok, "error": error, "data": data})


def write_failure(path: Path, error: str) -> None:
    """Record a failed fetch, keeping the last good data and its timestamp."""
    prev = read_cache(path)
    if prev is not None and prev["ok"] is False and prev.get("error") == error:
        # Identical failure envelope. Rewriting it changes nothing and costs an
        # fsync every cycle -- ~28,800/day onto the microSD during an outage,
        # which is the same wear this plan rejected a systemd timer to avoid.
        return
    _write(path, {
        "fetched_at": prev["fetched_at"] if prev else _now_iso(),
        "ok": False,
        "error": error,
        "data": prev["data"] if prev else {},
    })
