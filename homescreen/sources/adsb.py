# homescreen/sources/adsb.py
"""Fetch ADS-B traffic and cache it.

Runs as its own daemon on a cadence read from config, never on a device
request (VALIDATION C7). A looping daemon rather than a systemd timer so the
interval lives in config (VALIDATION C6), so ~29k interpreter spawns/day of
journal churn do not land on an SD card (CLAUDE.md §2), and so a slow upstream
cannot overlap requests and breach adsb.fi's 1 req/s limit.
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path

from homescreen.cache import write_cache, write_failure
from homescreen.config import (device, feed_cache_path, feed_config,
                               load_config)
from homescreen.sources.adsb_map import map_aircraft

log = logging.getLogger(__name__)


def _record_failure(cache_path: Path, error: str) -> None:
    """write_failure does I/O and can raise -- a read-only SD card (the
    canonical Pi failure, CLAUDE.md §2) makes every write throw. fetch_radar's
    "never raises" has to survive that too, or each cycle exits run_forever and
    Restart=always turns it into a restart loop that spams the journal."""
    try:
        write_failure(cache_path, error)
    except Exception as exc:  # noqa: BLE001
        log.error("could not record failure %r: %s", error, exc)


KM_PER_NM = 1.852
# (connect, read). INVARIANT: connect + read + fetch_seconds < STALE_HORIZON_S
# (12.0, the firmware's kExtrapolationHorizonSec). run_forever sleeps between
# requests, so one cycle costs roughly N x request + interval. Exceed
# 12 s and feed.age_s crosses the device's
# hard horizon on a healthy feed, dimming every target once per cycle --
# the blink pathology radar_display.cpp was rewritten to remove.
REQUEST_TIMEOUT_S = (3.05, 5)
STALE_HORIZON_S = 12.0
# adsb.fi public limit is 1 req/s. Enforced as spacing, not as an average.
MIN_REQUEST_SPACING_S = 1.0


def _number(dev: dict, label: str, value, lo: float, hi: float,
            *, inclusive_low: bool = True) -> float:
    """Range-check one numeric config field, or raise ValueError naming it.

    Rejects non-finite as well: `radius_km: .inf` passes any bare `< lo` test
    and then produces a nonsense `dist=inf` upstream request, and inf/nan is
    already rejected everywhere else in this project.
    """
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"device {dev['id']}: {label} must be a number, "
                         f"got {value!r}") from None
    if not math.isfinite(n):
        raise ValueError(f"device {dev['id']}: {label} must be finite, "
                         f"got {value!r}")
    too_low = n < lo if inclusive_low else n <= lo
    if too_low or n > hi:
        bound = f"{lo} to {hi}" if inclusive_low else f"above {lo}, up to {hi}"
        raise ValueError(f"device {dev['id']}: {label} must be {bound}, "
                         f"got {value!r}")
    return n


def check_config(cfg: dict, targets: list) -> None:
    """Reject a config that would run forever failing silently.

    Coercing malformed nodes (config.mapping) stops the daemon crashing, but on
    its own it turns a typo into a unit that is `active` and green while every
    fetch fails -- strictly worse than a loud stop, because nothing surfaces it.
    Anything that cannot be coerced to something usable is EX_CONFIG.
    """
    endpoint = feed_config(cfg).get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError(f"feeds.adsb.endpoint must be a non-empty string, "
                         f"got {endpoint!r}")

    for dev, _ in targets:
        if not isinstance(dev.get("id"), str):
            raise ValueError(f"device entry has no usable id: {dev!r}")
        home = dev.get("home")
        if not isinstance(home, dict):
            raise ValueError(f"device {dev['id']}: home must be a mapping, "
                             f"got {home!r}")
        _number(dev, "home.lat", home.get("lat"), -90.0, 90.0)
        _number(dev, "home.lon", home.get("lon"), -180.0, 180.0)
        # radius_km 0 would fetch nothing forever while the unit stays green --
        # the exact silent failure this function exists to prevent -- so the
        # lower bound is exclusive. max_aircraft 1 is odd but usable.
        _number(dev, "radius_km", dev.get("radius_km", 60), 0.0, 20000.0,
                inclusive_low=False)
        _number(dev, "max_aircraft", dev.get("max_aircraft", 20), 1.0, 1000.0)
        # X-Poll-Seconds is config's projection (CLAUDE.md), so a dangling
        # `poll_seconds:` would serve the device the literal string "None".
        _number(dev, "poll_seconds", dev.get("poll_seconds", 5), 1.0, 3600.0)

    check_cadence(cfg, len(targets))


def check_cadence(cfg: dict, n_targets: int = 1) -> None:
    """Fail loudly at startup rather than blinking mysteriously at runtime.

    Two independent limits, and BOTH scale with the number of devices:

    * Staleness. run_forever fetches each target and sleeps between them, so a
      device's cache can go `N x full_timeout + interval` between writes. A
      single-target budget silently green-lights a violating config the moment
      a second radar is registered. Note `read` is `requests`' inter-byte gap,
      not a total-response deadline, so a slow-drip response can still succeed
      past this budget -- the check is a startup heuristic, not a bound.
    * Rate. adsb.fi's public limit is 1 req/s, and a limiter enforces
      *spacing*, not an average -- N requests fired back to back inside one
      second breach it however long the cycle is.
    """
    raw = feed_config(cfg).get("fetch_seconds", 3)
    try:
        interval = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"fetch_seconds must be a number, got {raw!r}") from None
    n = max(1, int(n_targets))

    budget = n * sum(REQUEST_TIMEOUT_S) + interval
    if budget >= STALE_HORIZON_S:
        raise ValueError(
            f"fetch_seconds={interval} across {n} device(s) leaves a worst-case "
            f"dwell of {budget:.2f}s, at or past the firmware's "
            f"{STALE_HORIZON_S}s horizon")

    # NOTE: at the shipped REQUEST_TIMEOUT_S of (3.05, 5) the horizon check
    # above already rejects every N > 1 -- a second radar is not supportable
    # without shorter timeouts, and the 12 s horizon is fixed by firmware. That
    # is a real constraint, not an oversight: better surfaced at startup than
    # discovered as targets blinking once per cycle.
    spacing = interval / n
    if spacing < MIN_REQUEST_SPACING_S:
        raise ValueError(
            f"fetch_seconds={interval} across {n} device(s) spaces requests "
            f"{spacing:.2f}s apart, inside adsb.fi's "
            f"{MIN_REQUEST_SPACING_S}s limit")


def _url(endpoint: str, lat: float, lon: float, radius_km: float) -> str:
    dist_nm = radius_km / KM_PER_NM
    return (f"{endpoint.rstrip('/')}/lat/{lat:.6f}"
            f"/lon/{lon:.6f}/dist/{dist_nm:.1f}")


def fetch_radar(cfg: dict, dev: dict, cache_path: Path, *, session=None) -> bool:
    """Fetch, map and cache for ONE device. True on success, False on any
    failure. Never raises -- every failure path, including recording the
    failure itself, is guarded.

    `dev` is passed in rather than looked up by the literal "radar": the cache
    is per-device, so the parameters must be too.
    """
    if session is None:
        import requests
        session = requests.Session()

    try:
        # These reads are INSIDE the try on purpose. `radius_km: "60 km"` is a
        # units typo a human writes, and `float()` on it raises straight out of
        # run_forever into a Restart=always loop. Same for a `feeds` that is a
        # list, or a `feeds.adsb` that is a string. "Never raises" has to cover
        # malformed config, not just a malformed response.
        home = dev.get("home", {})
        radius_km = float(dev.get("radius_km", 60))
        endpoint = feed_config(cfg).get("endpoint", "")
        url = _url(endpoint, float(home.get("lat", 0.0)),
                   float(home.get("lon", 0.0)), radius_km)
        resp = session.get(url, timeout=REQUEST_TIMEOUT_S)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - nothing may escape into the cache
        log.warning("adsb fetch failed: %s", exc)
        _record_failure(cache_path, str(exc))
        return False

    raw_list = payload.get("ac") if isinstance(payload, dict) else None
    if not isinstance(raw_list, list):
        # An empty sky is `[]` and nothing else. A missing/null/scalar `ac`, or
        # an HTML captive-portal page, is not this API -- and treating it as "no
        # traffic" wipes real aircraft off the panel. The firmware refuses it
        # explicitly (adsb_client.cpp:355-366, "the last good list is kept until
        # it expires"); moving the fetch here REMOVES that guard, because we
        # would hand the device a well-formed empty list its own check passes.
        log.warning("adsb: response is not the aircraft feed")
        _record_failure(cache_path, "response is not the aircraft feed")
        return False

    radius_nm = radius_km / KM_PER_NM
    show_ground = bool(dev.get("show_ground", False))
    aircraft = []
    try:
        aircraft = _map_all(raw_list, radius_nm, show_ground)
        # write_cache is INSIDE the try on purpose: it refuses to serialise a
        # non-finite, and that refusal must become a recorded failure rather
        # than an exception escaping into run_forever's loop.
        write_cache(cache_path,
                    {"aircraft": aircraft[:int(dev.get("max_aircraft", 20))]})
    except Exception as exc:  # noqa: BLE001 - "never raises" must be structural
        log.warning("adsb mapping failed: %s", exc)
        _record_failure(cache_path, f"mapping failed: {exc}")
        return False
    return True


def _map_all(raw_list: list, radius_nm: float, show_ground: bool) -> list:
    aircraft = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        mapped = map_aircraft(raw, show_ground=show_ground)
        if mapped is None:
            continue
        # Upstream bounds by a rounded `dist`; enforce our own radius too.
        # A record with lat/lon but no `dst` is still drawable -- the firmware
        # keeps it (`dst_nm = ... : -1.0f`) and projects from lat/lon -- so only
        # drop records we can positively place outside the ring.
        if mapped["dst"] >= 0 and mapped["dst"] > radius_nm:
            continue
        aircraft.append(mapped)

    # Nearest first so the cap keeps what matters; dst-less records sort last.
    aircraft.sort(key=lambda a: a["dst"] if a["dst"] >= 0 else float("inf"))
    return aircraft


def fetch_targets(cfg: dict, cache_dir: Path) -> list:
    """Every data-push device on the adsb feed, with its own cache file.

    Per-device rather than a hardcoded "radar": `home`/`radius_km` are
    per-device, so each gets its own cache.

    N devices means N upstream requests per cycle. check_cadence enforces both
    limits that implies -- staleness and request spacing -- and at the shipped
    timeouts it rejects N > 1 outright. Registering a second radar therefore
    requires lowering REQUEST_TIMEOUT_S first.
    """
    devices = cfg.get("devices")
    if not isinstance(devices, list):
        return []
    return [(d, feed_cache_path(cache_dir, d))
            for d in devices
            if isinstance(d, dict) and d.get("id")
            and d.get("render") == "device" and d.get("feed") == "adsb"]


def run_forever(cfg: dict, targets: list, *, session=None, sleep=time.sleep) -> None:
    """`session` and `sleep` are injectable so the loop itself is testable --
    check_cadence's budget is only correct because of what this loop does, and
    a comment is not a guarantee."""
    if not targets:
        # Without this the `for` body never runs and the `while` spins at 100%
        # CPU. main() guards it too, but the loop must not depend on that.
        raise ValueError("run_forever needs at least one target")
    interval = float(feed_config(cfg).get("fetch_seconds", 3))
    if session is None:
        import requests
        session = requests.Session()
    spacing = interval / max(1, len(targets))
    log.info("adsb fetch loop starting for %s, interval %.1fs, spacing %.2fs",
             [d["id"] for d, _ in targets], interval, spacing)
    while True:
        for dev, cache_path in targets:
            fetch_radar(cfg, dev, cache_path, session=session)
            # Sleep AFTER each request, so a slow upstream stretches the cycle
            # instead of queueing a second in-flight request, and so N devices
            # are spaced rather than bursting inside one second.
            sleep(spacing)


def startup(root: Path) -> tuple[dict, list]:
    """Load and validate config. Raises SystemExit(78) on any config fault.

    Extracted from main() so the guard is TESTABLE. While this lived inline,
    six mutations survived the whole suite -- including shrinking the try back
    to `load_config` only, which silently re-introduces the round-8 restart
    loop. A guard nothing exercises is a comment.

    ONE handler spans load AND interpretation. The fetch loop stays OUTSIDE it:
    a runtime fault must not be reported as EX_CONFIG.
    """
    try:
        cfg = load_config(root / "config.yaml")
        targets = fetch_targets(cfg, root / "cache")
        if not targets:
            raise ValueError("no device with render: device and feed: adsb")
        check_config(cfg, targets)
    except Exception as exc:  # noqa: BLE001 - yaml.YAMLError and AttributeError
        log.exception("bad config: %s", exc)   # keep the stack: a code fault
        raise SystemExit(78) from None         # here reads as a config fault
    return cfg, targets


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg, targets = startup(Path(__file__).resolve().parents[2])
    run_forever(cfg, targets)


if __name__ == "__main__":
    main()
