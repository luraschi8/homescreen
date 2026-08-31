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
import re
from urllib.parse import quote, quote_plus
import time

from homescreen.fetch import plan as planning, providers, store

log = logging.getLogger(__name__)

#: How long a cycle may spend before re-reading the fleet. Assignments change
#: while this runs -- somebody is on the dashboard -- and a daemon that only
#: learns about a new screen on restart is a daemon someone has to remember to
#: restart.
RELOAD_EVERY_S = 30.0


def due(job, last_run: dict, now: float) -> bool:
    ran = last_run.get(job.key)
    return ran is None or (now - ran) >= job.interval_s


#: Query parameters that carry credentials. Vendors disagree about the name,
#: and every one of them puts it in the URL that ends up in an exception.
_SECRET_PARAMS = ("token", "key", "apikey", "api_key", "appid", "access_key",
                  "auth", "password", "secret")

_SECRET_IN_URL = re.compile(
    r"([?&](?:" + "|".join(_SECRET_PARAMS) + r")=)[^&\s\"\']+",
    re.IGNORECASE)


def redact(text: str, values=None) -> str:
    """Strip credentials out of a message before it is stored or shown.

    Found by running a real request: Finnhub answered 403, and `requests` put
    the whole URL -- including `&token=...` -- into the exception. That string
    goes into the job envelope, which `/api/jobs` and the settings page render
    on an unauthenticated LAN dashboard. One failed fetch published the key.

    Two passes, because either alone is insufficient. The pattern catches a
    credential in a URL whatever its value; the exact values catch a key that
    appears somewhere the pattern does not expect -- a body echo, a header
    dump, a vendor's own error text quoting it back.
    """
    out = _SECRET_IN_URL.sub(r"\1[oculto]", str(text))
    for value in values or ():
        value = str(value or "")
        # Three characters, not eight. The old floor exempted every short key
        # from a redaction whose whole point is that it has no exceptions; the
        # floor exists only so a one-character credential cannot blank out the
        # message it was meant to make safe.
        if len(value) < 3:
            continue
        # A vendor quotes the request back re-encoded, so the key arrives as
        # `a%20b` and a plain replace walks past it.
        for form in {value, quote(value, safe=""), quote_plus(value)}:
            out = out.replace(form, "[oculto]")
    return out


def _record_failure_safely(cache_dir, key: str, error: str) -> None:
    """Record a failure, and never fail doing it.

    `record_failure` can itself raise -- an unusable key, or a read-only
    filesystem, which is the exact Pi fault `sources/adsb.py` wraps for. The
    handler calling it was the LAST guard, so its own exception escaped a
    function documented as never raising, straight into a Restart=always loop.
    """
    try:
        store.record_failure(cache_dir, key, error)
    except Exception:                                   # noqa: BLE001
        log.warning("could not record the failure of job %s", key,
                    exc_info=True)


def run_once(cache_dir, plan: dict, last_run: dict, *, now: float,
             session=None, secrets_for=None, sleep=None) -> int:
    """Fetch every job that is due. Returns how many ran. Never raises.

    One failing provider must not stop the others: a stocks API being down is
    not a reason for the radar to stop.
    """
    ran = 0
    # Upstream politeness is per PROVIDER, not per job: five radars on five
    # centres each politely scheduled still fire together. Spacing is applied
    # between requests to the same provider, in one cycle.
    last_request: dict = {}
    for job in sorted(plan.values(), key=lambda j: j.key):
        if not due(job, last_run, now):
            continue
        provider = providers.get(job.provider)
        if provider is None:
            # Recorded as attempted anyway, or a job the runner keeps skipping
            # makes `_nap` compute a negative wait and pins the loop at its
            # floor -- a 2Hz spin on a Pi with nothing in the log to explain it.
            log.warning("job %s names provider %r, which is not registered",
                        job.key, job.provider)
            last_run[job.key] = now
            continue
        gap = providers.min_spacing(job.provider)
        if gap and job.provider in last_request and sleep:
            waited = time.monotonic() - last_request[job.provider]
            if waited < gap:
                sleep(gap - waited)
        last_request[job.provider] = time.monotonic()
        last_run[job.key] = now
        ran += 1
        # Bound BEFORE the try, not inside it: the handler below reads `creds`,
        # and `secrets_for` reads a file that can refuse. An unbound name there
        # escapes `run_once` into a `Restart=always` loop.
        creds = None
        try:
            # Scoped per provider: a weather key must not be reachable from
            # the quotes adapter. The narrow port makes that enforceable
            # rather than a convention nobody checks.
            creds = (secrets_for(job.provider,
                                 job.params.get("secret_scope"))
                     if secrets_for else None)
            # The adapter is handed its parameters without the bookkeeping:
            # `secret_scope` says WHICH key, which the runner has already
            # resolved into `creds`.
            params = {k: v for k, v in job.params.items()
                      if k != "secret_scope"}
            payload = provider.fetch(params, session=session, secrets=creds)
        except Exception as exc:                        # noqa: BLE001
            # Redacted BEFORE it is logged or stored: the log is on disk and
            # the store is rendered on an unauthenticated page.
            safe = redact(exc, (creds or {}).values())
            log.warning("job %s failed: %s", job.key, safe)
            _record_failure_safely(cache_dir, job.key, safe)
            continue
        try:
            store.save(cache_dir, job.key, payload)
        except Exception as exc:                        # noqa: BLE001
            # write_cache refuses to serialise a non-finite, and that refusal
            # must become a recorded failure rather than an exception escaping
            # into a Restart=always loop.
            safe = redact(exc, (creds or {}).values())
            log.warning("job %s could not be stored: %s", job.key, safe)
            _record_failure_safely(cache_dir, job.key,
                                   f"store failed: {safe}")
    return ran


def run_forever(cfg_loader, records_loader, cache_dir, *, session=None,
                sleep=time.sleep, clock=time.time, cycles: int | None = None,
                secrets_for=None, has_own_key=None):
    """Re-derive the plan, fetch what is due, prune what nobody wants."""
    last_run: dict = {}
    last_reload = 0.0
    plan: dict = {}
    done = 0
    while cycles is None or done < cycles:
        now = clock()
        if now - last_reload >= RELOAD_EVERY_S or not plan:
            plan = planning.collect(records_loader(), cfg_loader(),
                                has_own_key=has_own_key)
            last_reload = now
            store.prune(cache_dir, set(plan))
            # Forget the schedule of jobs nobody wants, so a job that comes
            # back later fetches immediately rather than appearing stale.
            for key in list(last_run):
                if key not in plan:
                    last_run.pop(key, None)
        run_once(cache_dir, plan, last_run, now=now, session=session,
                 secrets_for=secrets_for, sleep=sleep)
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
