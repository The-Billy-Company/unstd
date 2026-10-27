"""Captured Consumer randomness consumers retain source and effect checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.profiles import consumer_rand as profile
from bench.profiles import rand_backend as timing
from unstd.rand import draw


FIXTURES = Path(profile.__file__).resolve().parents[1] / "workloads/consumer_rand.json"


@pytest.mark.parametrize(
    "replacement",
    ["return shuffled", "return 'keywords=[]'", "return shuffled[:cap] + []"],
)
def test_captured_function_edits_cannot_retain_an_old_source_receipt(
    tmp_path, replacement
):
    captured = json.loads(FIXTURES.read_text())
    captured["consumers"]["corpus_trim"]["source"] = captured["consumers"][
        "corpus_trim"
    ]["source"].replace("return shuffled[:cap]", replacement)
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(captured))
    with pytest.raises(ValueError, match="AST disagrees"):
        profile._load_consumers(changed)


def test_workflow_cases_have_independent_inputs_and_exact_seeded_effects():
    trees, _evidence = profile._load_consumers(FIXTURES)
    path = Path(draw.__file__)
    current, _ = timing._load_source(path)
    before, _ = timing._load_source(path)
    cases = profile._cases({"current": current, "before": before}, trees)
    assert len(cases) == 13
    for case in cases:
        contexts = {label: factory() for label, factory in case.factories.items()}
        assert contexts["current"].examples is not contexts["before"].examples
        for _ in range(2):
            outputs = {
                label: case.operation(context) for label, context in contexts.items()
            }
            timing._check_pair(outputs, contexts)


def test_masking_control_keeps_exact_original_selection_and_hash_side_effects():
    trees, _evidence = profile._load_consumers(FIXTURES)
    current = profile.Context(draw, trees, 256)
    control = profile.Context(draw, trees, 256)
    assert profile._masking(current) == profile._masking_control(control)
    assert [example.mask_seed for example in current.examples] == [
        example.mask_seed for example in control.examples
    ]
    # Internal generator construction belongs to the real end-to-end workload.
    assert len(current.created) == len(control.created) + 1


def test_output_check_detects_side_effect_changes_without_timed_normalization():
    trees, _evidence = profile._load_consumers(FIXTURES)
    current = profile.Context(draw, trees, 32)
    before = profile.Context(draw, trees, 32)
    current.examples[0].mask_seed = 9
    with pytest.raises(AssertionError, match="state consumption"):
        timing._check_pair(
            {"current": 0, "before": 0}, {"current": current, "before": before}
        )


def test_speed_target_excludes_controls_and_records_an_honest_failure():
    workloads = [
        {
            "name": "consumer-trim-256",
            "median_matched_before_over_current": {"wall_ns": 3.0},
        },
        {
            "name": "consumer-schema-16",
            "median_matched_before_over_current": {"wall_ns": 1.0},
        },
        {
            "name": "consumer-function-masking-selection-precomputed-256",
            "median_matched_before_over_current": {"wall_ns": 0.1},
        },
    ]
    result = profile._target_summary(workloads, 100)
    assert result["eligible_real_consumers"] == 2
    assert result["all_real_consumers_passed"] is False
    assert result["minimum_before_over_current"] == 1
    assert workloads[0]["speedup_target"]["passed"] is False
    assert workloads[2]["speedup_target"]["passed"] is None
