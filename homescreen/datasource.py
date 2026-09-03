"""Resolving a component's declared requirement to what was actually fetched.

The adapter side of `SceneContext.data`. It lives here rather than inside
`serve.py` because the preview, the device route and the tests must all resolve
identically -- a preview drawn from a different source than the device is a
guess, which is the same failure the two-executor design exists to prevent.
"""

from __future__ import annotations

from homescreen.fetch import providers, store
from homescreen.reading import Reading


def reader(cache_dir, now_fn, scope: str | None = None):
    """A `data` port bound to one cache, one clock, and one placement.

    Returns a callable taking the requirement a component declared and giving
    back a Reading. The component never learns that a job exists, where the
    payload is cached, or whether the last fetch succeeded -- only what it has
    and how old it is.

    `scope` is `{hw}/{view}/{placement}`, and it exists because a placement
    with its OWN credential is a different fetch: `fetch.plan` puts that scope
    into the job's parameters, so the job is keyed differently. Reading without
    it looked up a job that had never run -- the panel said "sin datos" while a
    perfectly good payload sat on disk under the scoped name, which is exactly
    the confident-nothing failure this codebase keeps having to fix.

    The scoped payload wins and the unscoped one is the fallback, mirroring
    `secrets.for_provider`: a screen with its own key uses it, one without
    falls back rather than failing.
    """
    def read(requirement):
        if not isinstance(requirement, dict):
            return Reading.nothing()
        provider = requirement.get("provider")
        try:
            params = providers.clean_params(provider, requirement.get("params"))
        except ValueError:
            return Reading.nothing()
        if scope:
            scoped = providers.clean_params(
                provider, {**(requirement.get("params") or {}),
                           "secret_scope": f"{scope}/{provider}"})
            env = store.read(cache_dir, providers.key(provider, scoped))
            if env:
                return Reading.from_envelope(env, now=now_fn())
        env = store.read(cache_dir, providers.key(provider, params))
        return Reading.from_envelope(env, now=now_fn())
    return read
