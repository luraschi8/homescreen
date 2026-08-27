"""Running the jobs the fleet implies, on each one's own cadence.

The loop owns three things the adapters deliberately do not: WHEN to fetch,
what a failure means, and where the payload goes. That is why a provider's
`fetch` raises instead of returning a status -- it has no opinion about any of
them, and could not have a useful one.

Injectable clock and sleep, because the cadence is the behaviour under test and
a comment claiming "it waits the right amount" is not a guarantee.
"""

from __future__ import annotations

import logging
import time

from homescreen import jobs, jobstore, providers

log = logging.getLogger(__name__)

#: How long a cycle may spend before re-reading the fleet. Assignments change
#: while this runs -- somebody is on the dashboard -- and a daemon that only
#: learns about a new screen on restart is a daemon someone has to remember to
#: restart.
RELOAD_EVERY_S = 30.0


def due(job, last_run: dict, now: float) -> bool:
    ran = last_run.get(job.key)
    return ran is None or (now - ran) >= job.interval_s


def run_once(cache_dir, plan: dict, last_run: dict, *, now: float,
             session=None, secrets_for=None) -> int:
    """Fetch every job that is due. Returns how many ran. Never raises.

    One failing provider must not stop the others: a stocks API being down is
    not a reason for the radar to stop.
    """
    ran = 0
    for job in plan.values():
        if not due(job, last_run, now):
            continue
        provider = providers.get(job.provider)
        if provider is None:
            continue
        last_run[job.key] = now
        ran += 1
        try:
            # Scoped per provider: a weather key must not be reachable from
            # the quotes adapter. The narrow port makes that enforceable
            # rather than a convention nobody checks.
            creds = secrets_for(job.provider) if secrets_for else None
            payload = provider.fetch(job.params, session=session,
                                     secrets=creds)
        except Exception as exc:                        # noqa: BLE001
            log.warning("job %s failed: %s", job.key, exc)
            jobstore.record_failure(cache_dir, job.key, str(exc))
            continue
        try:
            jobstore.store(cache_dir, job.key, payload)
        except Exception as exc:                        # noqa: BLE001
            # write_cache refuses to serialise a non-finite, and that refusal
            # must become a recorded failure rather than an exception escaping
            # into a Restart=always loop.
            log.warning("job %s could not be stored: %s", job.key, exc)
            jobstore.record_failure(cache_dir, job.key, f"store failed: {exc}")
    return ran


def run_forever(cfg_loader, records_loader, cache_dir, *, session=None,
                sleep=time.sleep, clock=time.time, cycles: int | None = None,
                secrets_for=None):
    """Re-derive the plan, fetch what is due, prune what nobody wants."""
    last_run: dict = {}
    last_reload = 0.0
    plan: dict = {}
    done = 0
    while cycles is None or done < cycles:
        now = clock()
        if now - last_reload >= RELOAD_EVERY_S or not plan:
            plan = jobs.collect(records_loader(), cfg_loader())
            last_reload = now
            jobstore.prune(cache_dir, set(plan))
            # Forget the schedule of jobs nobody wants, so a job that comes
            # back later fetches immediately rather than appearing stale.
            for key in list(last_run):
                if key not in plan:
                    last_run.pop(key, None)
        run_once(cache_dir, plan, last_run, now=now, session=session,
                 secrets_for=secrets_for)
        done += 1
        if cycles is None or done < cycles:
            sleep(_nap(plan, last_run, clock()))


def _nap(plan: dict, last_run: dict, now: float) -> float:
    """Until the soonest job is due, bounded.

    Not a fixed tick: a fleet of one weather screen should not wake the Pi
    every second, and a radar at 5s should not wait on a weather job's hour.
    """
    if not plan:
        return 5.0
    waits = [max(0.0, job.interval_s - (now - last_run.get(job.key, 0.0)))
             for job in plan.values()]
    return max(0.5, min(min(waits), RELOAD_EVERY_S))
