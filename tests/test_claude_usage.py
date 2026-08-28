"""Token spend on a wall.

The response shape is the one thing here I could not verify against a real
organisation, so these pin the DEFENSIVE behaviour: what it does when the shape
is not what was assumed. A wrong number looks exactly like a right one.
"""
import pathlib
import tempfile

import pytest

from homescreen import fetch, scenes
from homescreen.fetch.providers import claude_usage
from homescreen.reading import Reading

ROUND = {"w": 240, "h": 240, "depth": 16, "shape": "round"}


def drawn(reading, options=None):
    ctx = scenes.SceneContext(
        cfg={}, cache_dir=pathlib.Path(tempfile.mkdtemp()), caps=ROUND, now=0,
        device={}, options=options or {"days": 30}, data=lambda req: reading)
    return [d["v"] for d in scenes.build("claude", ctx).components[0]["draw"]]


def test_a_number_a_person_reads_from_across_the_room():
    values = drawn(Reading(data={"total_tokens": 2_500_000,
                                 "input_tokens": 2_000_000,
                                 "output_tokens": 500_000, "days": 30}, ok=True))
    assert values[0] == "2.5M", "magnitude, not 2500000"
    assert "30 días" in values[1]


def test_with_no_data_it_says_so_rather_than_showing_zero():
    # Zero is a number somebody would believe.
    values = drawn(Reading.nothing())
    assert values[0] == "--"
    assert any("sin datos" in v for v in values)


def test_a_report_whose_shape_is_not_what_we_assumed_is_a_failure():
    # The parsing here is unverified against a real organisation. Returning
    # zero would put a confident, wrong number on a wall.
    assert claude_usage._totals({"error": "unauthorized"}) is None
    assert claude_usage._totals([]) is None
    assert claude_usage._totals(None) is None


def test_a_bucketed_report_is_summed_at_whatever_depth_it_arrives():
    body = {"data": [{"results": [{"input_tokens": 1_200_000,
                                   "output_tokens": 340_000}]},
                     {"results": [{"input_tokens": 800_000,
                                   "output_tokens": 160_000}]}]}
    got = claude_usage._totals(body)
    assert got["input_tokens"] == 2_000_000
    assert got["output_tokens"] == 500_000
    assert got["total_tokens"] == 2_500_000


def test_the_provider_refuses_to_fetch_without_an_admin_key():
    with pytest.raises(ValueError, match="administración"):
        claude_usage.fetch({"days": 30}, secrets={})


def test_the_key_travels_as_a_header():
    captured = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"input_tokens": 1, "output_tokens": 1}]}

    class Session:
        def get(self, url, **kw):
            captured.update(kw)
            return Resp()

    claude_usage.fetch({"days": 7}, session=Session(),
                       secrets={"admin_key": "sk-admin"})
    assert captured["headers"]["x-api-key"] == "sk-admin"
    assert captured["headers"]["anthropic-version"]


def test_the_period_is_bounded():
    clean = lambda d: fetch.providers.clean_params("claude_usage", d)["days"]
    # Zero means "unset" and takes the default, the same way it does for the
    # radar's radius -- one idiom across the components, not two.
    assert clean({"days": 0}) == 30
    assert clean({"days": "x"}) == 30
    # A value that is meant but unusable is clamped rather than replaced.
    assert clean({"days": -5}) == 1
    assert clean({"days": 9999}) == 90


def test_two_screens_over_the_same_period_share_one_fetch():
    plan = fetch.derive({"a": {"scene": "claude", "options": {"days": 30}},
                         "b": {"scene": "claude", "options": {"days": 30}}}, {})
    assert len(plan) == 1


def test_different_periods_are_different_fetches():
    plan = fetch.derive({"a": {"scene": "claude", "options": {"days": 7}},
                         "b": {"scene": "claude", "options": {"days": 30}}}, {})
    assert len(plan) == 2
