"""Data acquisition: what to fetch, how often, from where, and where it lands.

One bounded context with three roles inside it, which is why these are one
package rather than four top-level modules a reader has to assemble mentally:

    plan.py       what the fleet's assignments imply. Pure -- records in, jobs
                  out, no network, no clock, no cache.
    providers/    adapters. Each knows how to fetch one kind of data and
                  nothing about screens.
    runner.py     when to fetch, what a failure means, where the payload goes.
    store.py      naming over the cache envelope.

The public surface is here so callers say `fetch.derive(...)` and
`fetch.run_once(...)` rather than reaching into the parts.
"""

from homescreen.fetch import providers
from homescreen.fetch.plan import Job, collect as derive
from homescreen.fetch.runner import run_forever, run_once
# `save`, not `store`: the module is `fetch.store`, and exporting a function
# of the same name shadowed it -- `fetch.store.path_for` resolved to an
# attribute of the function and every caller broke at once.
from homescreen.fetch.store import path_for, prune, read, record_failure, save

__all__ = ["Job", "derive", "providers", "run_forever", "run_once",
           "path_for", "prune", "read", "record_failure", "save"]
