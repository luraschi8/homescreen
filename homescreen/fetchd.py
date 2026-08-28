"""The fetch daemon: run whatever the fleet's assignments imply.

Replaces `python -m homescreen.sources.adsb`, which was one loop for one feed
and assumed exactly one radar. This runs every provider, on each job's own
cadence, deriving the work from what screens are configured to show.

It exits 78 (EX_CONFIG) on unusable config, matching the old behaviour, so
systemd's Restart=always does not spin on a fault a human has to fix.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homescreen import jobrunner, overrides, registry, secrets
from homescreen.config import load_config

log = logging.getLogger("homescreen.fetchd")


def _readers(root: Path):
    """Re-read config and fleet on every cycle rather than closing over them.

    Both change while this runs: somebody is on the dashboard assigning a
    screen, or has just changed the upstream. A daemon that learns about either
    only on restart is one somebody has to remember to restart.
    """
    cache_dir = root / "cache"

    def cfg():
        return overrides.apply(load_config(root / "config.yaml"), cache_dir)

    def records():
        return registry.load(cache_dir)

    return cfg, records, cache_dir


def _with_secrets(cache_dir: Path):
    """Hand each provider its own credentials and nobody else's.

    Scoped per provider on purpose: a weather key must not be reachable from
    the quotes adapter, and the narrow port is what makes that enforceable
    rather than a convention.
    """
    def fetch_with(provider_module, params, session):
        return provider_module.fetch(
            params, session=session,
            secrets=secrets.for_provider(cache_dir, provider_module.NAME))
    return fetch_with


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    root = Path(__file__).resolve().parents[1]
    try:
        cfg_loader, records_loader, cache_dir = _readers(root)
        cfg_loader()                      # fail fast on unreadable config
    except Exception as exc:              # noqa: BLE001
        log.exception("bad config: %s", exc)
        raise SystemExit(78) from None
    log.info("fetch daemon starting; work is derived from assignments")
    from homescreen import providers

    def has_own_key(provider, scope):
        return any(secrets.has(cache_dir, provider, name, scope)
                   for name in providers.secrets_for(provider))

    jobrunner.run_forever(cfg_loader, records_loader, cache_dir,
                          has_own_key=has_own_key,
                          secrets_for=lambda name, scope=None:
                              secrets.for_provider(cache_dir, name, scope))


if __name__ == "__main__":
    main()
