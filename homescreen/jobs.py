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


def requirements_of(component: str, options: dict, cfg: dict) -> tuple:
    """What one component needs fetched, given how it is configured.

    A function of its OPTIONS, because that is what makes it specific: the
    weather component needs a place, the quotes component needs symbols, and
    which ones is the assignment's business.
    """
    from homescreen import scenes
    return scenes.needs(component, options, cfg)


def collect(records: dict, cfg: dict) -> dict:
    """{key: Job} for the whole fleet. Never raises.

    Walks every device, every view it can show -- not merely the one showing
    now. A schedule that switches to weather at 07:00 must not discover at
    07:00 that nobody has been fetching weather; the job exists because the
    view exists, and it is warm before it is needed.
    """
    found: dict = {}
    for hw, rec in (records or {}).items():
        if not isinstance(rec, dict):
            continue
        for view_name in layout.view_names(rec):
            view = layout.view_for(rec, view_name)
            for placement in view.get("placements") or ():
                component = placement.get("component")
                options = placement.get("options") or {}
                try:
                    needs = requirements_of(component, options, cfg)
                except Exception:                       # noqa: BLE001
                    continue                            # a scene may not need any
                for need in needs or ():
                    job = _job_from(need, f"{hw}/{view_name}")
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
