"""Credentials a provider needs, settable from the dashboard and never read back.

The rule this module exists to make structural: a secret can be WRITTEN through
the API and can never be READ through it. Not "is redacted in the usual
response" -- there is no code path that returns a value, so a new endpoint
cannot leak one by forgetting to redact. CLAUDE.md 7.4 says the status page is
unauthenticated on the LAN and must render config STRUCTURE only; the way to
keep that true as the API grows is to make the value unreachable rather than
carefully omitted.

What the dashboard gets instead is whether a secret is set and when it changed,
which is everything needed to answer "why is weather failing" without exposing
the key to whoever is on the network.

Stored outside config.yaml, in the cache directory, mode 0600. config.yaml is
hand-edited and lives in git; a value typed into a web form should not end up
in either.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

#: Names are chosen by providers, but reach this module from stored records and
#: from URLs, so they are validated rather than trusted.
NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")

#: A per-assignment credential is stored under the same provider, with the
#: placement it belongs to appended. Scopes come from hardware ids and view
#: names, so they are constrained the same way everything else that becomes a
#: key is.
SCOPE_RE = re.compile(r"^[A-Za-z0-9_:./-]{1,120}$")

#: Separator between a credential's name and the placement that owns it. Not a
#: character either side can contain, so the split is unambiguous.
SCOPE_SEP = "@"

MAX_VALUE_LEN = 4096
MAX_SECRETS = 64


def secrets_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "secrets.json"


def scoped(name: str, scope=None) -> str:
    """The storage name for a credential, global or per placement."""
    if not scope:
        return str(name)
    return f"{name}{SCOPE_SEP}{scope}"


def _check_scope(scope) -> str:
    if scope in (None, ""):
        return ""
    if not SCOPE_RE.match(str(scope)):
        raise ValueError(f"ámbito no válido: {scope!r}")
    return str(scope)


def _check(provider: str, name: str) -> tuple[str, str]:
    if not NAME_RE.match(str(provider or "")):
        raise ValueError(f"proveedor no válido: {provider!r}")
    if not NAME_RE.match(str(name or "")):
        raise ValueError(f"nombre de secreto no válido: {name!r}")
    return str(provider), str(name)


def _load(cache_dir: Path) -> dict:
    """Never raises. A corrupt file degrades to "nothing is set", which
    surfaces as a provider failing loudly rather than the daemon dying."""
    try:
        raw = json.loads(secrets_path(cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(cache_dir: Path, data: dict) -> None:
    path = secrets_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # Created 0600 BEFORE anything is written to it. Writing first and
    # chmod-ing after leaves a window where the key is world-readable, and on a
    # box whose whole security model is "the LAN is trusted" that window is the
    # one thing that has to be closed.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def set_secret(cache_dir: Path, provider: str, name: str, value: str,
               scope=None) -> dict:
    """Store a credential, globally or for one placement.

    A per-placement key is what lets two screens read two different accounts --
    a work calendar and a personal one, or two weather plans. Without a scope
    this is the deployment's own key, which every placement falls back to.
    """
    provider, name = _check(provider, name)
    name = scoped(name, _check_scope(scope))
    value = "" if value is None else str(value)
    if not value.strip():
        raise ValueError("el valor no puede estar vacío")
    if len(value) > MAX_VALUE_LEN:
        raise ValueError(f"el valor supera {MAX_VALUE_LEN} caracteres")
    data = _load(cache_dir)
    if len(data) >= MAX_SECRETS and provider not in data:
        raise ValueError(f"como máximo {MAX_SECRETS} proveedores con secretos")
    block = dict(data.get(provider) or {})
    block[name] = {"value": value, "updated_at": _stamp()}
    data[provider] = block
    _save(cache_dir, data)
    return status(cache_dir, provider, name)


def clear(cache_dir: Path, provider: str, name: str, scope=None) -> bool:
    provider, name = _check(provider, name)
    name = scoped(name, _check_scope(scope))
    data = _load(cache_dir)
    block = data.get(provider)
    if not isinstance(block, dict) or name not in block:
        return False
    block.pop(name)
    if block:
        data[provider] = block
    else:
        data.pop(provider, None)
    _save(cache_dir, data)
    return True


def status(cache_dir: Path, provider: str, name: str, scope=None) -> dict:
    """Whether it is set and when it changed. There is deliberately no `value`
    key -- absent, not null, so nothing downstream can serialise a placeholder
    into a field an operator later mistakes for the secret."""
    try:
        provider, bare = _check(provider, name)
        stored = scoped(bare, _check_scope(scope))
    except ValueError:
        return {"provider": provider, "name": name, "set": False}
    entry = (_load(cache_dir).get(provider) or {}).get(stored)
    out = {"provider": provider, "name": bare, "set": isinstance(entry, dict)}
    if scope:
        out["scope"] = str(scope)
    if isinstance(entry, dict):
        out["updated_at"] = entry.get("updated_at")
    return out


def statuses(cache_dir: Path, provider: str, names) -> list:
    return [status(cache_dir, provider, n) for n in names or ()]


def has(cache_dir: Path, provider: str, name: str, scope=None) -> bool:
    """Whether a credential exists. The predicate job collection needs, so it
    can stay pure while still knowing that two screens use two keys."""
    return bool(status(cache_dir, provider, name, scope).get("set"))


def for_provider(cache_dir: Path, provider: str, scope=None) -> dict:
    """{name: value} for the FETCHER only.

    The one function that returns values, and the only caller that should ever
    reach it is the job runner handing them to an adapter. It is deliberately
    not exposed through any route: keeping the value unreachable from the API
    is structural here, not a matter of remembering to redact.
    """
    try:
        provider, _ = _check(provider, "x")
        scope = _check_scope(scope)
    except ValueError:
        return {}
    block = _load(cache_dir).get(provider)
    if not isinstance(block, dict):
        return {}
    # The deployment's keys first, then this placement's on top: a screen with
    # its own key uses it, and one without falls back rather than failing.
    out = {}
    for stored, entry in block.items():
        if not isinstance(entry, dict) or not entry.get("value"):
            continue
        bare, sep, owner = str(stored).partition(SCOPE_SEP)
        if not sep:
            out.setdefault(bare, entry["value"])
        elif scope and owner == scope:
            out[bare] = entry["value"]
    return out


def _stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
