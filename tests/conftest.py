"""Shared fixtures.

The frame cache is process-global by design -- it exists so a browser does not
fork on every poll -- which makes tests order-dependent if they assume a cold
one. Rather than clearing it before every test (which would re-render on each
of the ~20 tests that draw a frame, and made the suite 3x slower), tests that
need a cold cache ask for `cold_frame_cache` explicitly.

Found the hard way: a test that patched chromium away passed alone and failed
in the suite, because an earlier test had already cached the frame it expected
to fail on.
"""
import pytest

from homescreen import render


@pytest.fixture
def cold_frame_cache():
    """For tests whose subject is the render path itself."""
    render.clear_cache()
    yield
    render.clear_cache()


class FrozenClock:
    """A clock the test moves, not the wall.

    Scene HTML embeds `%H:%M`, so a real-clock test that polls twice across a
    minute boundary renders different HTML the second time -- a cold render,
    which the throttle answers with 429. That made three files intermittently
    red (3 failures in 12 runs) and, worse, silently corrupted a mutation
    sweep: three mutations were recorded as killed by a flake rather than by
    an assertion.
    """

    def __init__(self, t: float = 1_787_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


@pytest.fixture
def frozen_clock():
    return FrozenClock()

