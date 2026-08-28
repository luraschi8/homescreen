"""Resolving a component's declared requirement to what was actually fetched.

The adapter side of `SceneContext.data`. It lives here rather than inside
`serve.py` because the preview, the device route and the tests must all resolve
identically -- a preview drawn from a different source than the device is a
guess, which is the same failure the two-executor design exists to prevent.
"""

from __future__ import annotations

from homescreen.fetch import providers, store
from homescreen.reading import Reading


def reader(cache_dir, now_fn):
    """A `data` port bound to one cache and one clock.

    Returns a callable taking the requirement a component declared and giving
    back a Reading. The component never learns that a job exists, where the
    payload is cached, or whether the last fetch succeeded -- only what it has
    and how old it is.
    """
    def read(requirement):
        if not isinstance(requirement, dict):
            return Reading.nothing()
        provider = requirement.get("provider")
        try:
            params = providers.clean_params(provider, requirement.get("params"))
        except ValueError:
            return Reading.nothing()
        env = store.read(cache_dir, providers.key(provider, params))
        return Reading.from_envelope(env, now=now_fn())
    return read
