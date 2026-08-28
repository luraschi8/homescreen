"""What the fetch daemon should be fetching, derived from what screens show.

Nobody configures a job. A job is the CONSEQUENCE of an assignment: a screen
showing weather for Madrid implies somebody must fetch weather for Madrid, and
when that screen stops showing it, the job stops existing. Deriving the work
from the arrangement rather than storing it separately means the two can never
disagree -- there is no orphaned job for a screen that was removed, and no
screen waiting on a job nobody created.

This module is pure. It takes records and returns jobs; it does not fetch, does
not read the network, and does not touch a cache. That is what makes
deduplication and cadence testable without an upstream.
"""

from __future__ import annotations

import dataclasses

from homescreen import layout, providers


@dataclasses.dataclass(frozen=True)
class Job:
    """One fetch, and everyone who is waiting for it."""
    provider: str
    key: str
    params: dict
    interval_s: int
    #: Which screens want this, for the fleet view and for debugging. Not part
    #: of identity: the same data wanted by three screens is one job.
    wanted_by: tuple = ()

    def with_wanted(self, who) -> "Job":
        return dataclasses.replace(self, wanted_by=tuple(sorted(set(who))))


def _default_requirements(component: str, options: dict, cfg: dict) -> tuple:
    from homescreen import scenes
    return scenes.needs(component, options, cfg)


def collect(records: dict, cfg: dict, *, requirements=None,
            has_own_key=None) -> dict:
    """{key: Job} for the whole fleet. Never raises.

    Walks every device, every view it can show -- not merely the one showing
    now. A schedule that switches to weather at 07:00 must not discover at
    07:00 that nobody has been fetching weather; the job exists because the
    view exists, and it is warm before it is needed.

    `requirements` and `has_own_key` are injected so this stays pure and
    testable: the first is what a component needs, the second answers whether
    one placement has its OWN credential. That second question is what stops
    two screens reading two different accounts from silently sharing one fetch
    -- and one key winning.
    """
    requirements = requirements or _default_requirements
    found: dict = {}
    for hw, rec in (records or {}).items():
        if not isinstance(rec, dict):
            continue
        for view_name in layout.view_names(rec):
            view = layout.view_for(rec, view_name)
            for placement in view.get("placements") or ():
                component = placement.get("component")
                options = placement.get("options") or {}
                where = f"{hw}/{view_name}"
                needs = requirements(component, options, cfg)
                for need in needs or ():
                    # A placement with its own credential is a DIFFERENT fetch,
                    # even for identical parameters: same question, different
                    # account. The scope is an identifier, not a secret, so it
                    # is safe in the key and visible in /api/jobs.
                    if has_own_key and isinstance(need, dict):
                        scope = f"{where}/{(need.get('provider') or '')}"
                        if has_own_key(need.get("provider"), scope):
                            need = {**need,
                                    "params": {**(need.get("params") or {}),
                                               "secret_scope": scope}}
                    job = _job_from(need, where)
                    if job is None:
                        continue
                    existing = found.get(job.key)
                    if existing is None:
                        found[job.key] = job
                    else:
                        # Same data, two askers. One job, the shorter cadence,
                        # and both names on it.
                        found[job.key] = dataclasses.replace(
                            existing,
                            interval_s=min(existing.interval_s, job.interval_s),
                            wanted_by=tuple(sorted(set(existing.wanted_by)
                                                   | set(job.wanted_by))))
    return found


def _job_from(need, who: str):
    """One requirement as a job, or None if it names nothing usable."""
    if not isinstance(need, dict):
        return None
    provider = need.get("provider")
    if providers.get(provider) is None:
        return None
    try:
        params = providers.clean_params(provider, need.get("params"))
    except ValueError:
        # A requirement we cannot turn into a fetch is dropped rather than
        # guessed at: fetching the wrong thing forever looks healthy.
        return None
    interval = need.get("interval_s")
    interval = (providers.clamp_interval(interval) if interval is not None
                else providers.default_interval(provider))
    return Job(provider=provider, key=providers.key(provider, params),
               params=params, interval_s=interval, wanted_by=(who,))
