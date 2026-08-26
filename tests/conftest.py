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
