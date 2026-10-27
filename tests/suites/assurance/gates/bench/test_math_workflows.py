"""Benchmark confirmations preserve raw failures and require persistent evidence."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.support.environment.backends import require_backend


@pytest.fixture
def profile():
    require_backend("numpy")
    from bench.profiles import math_workflows

    return math_workflows


def _row(failures, sample):
    return {
        "material_regression_vs": failures,
        "lanes": {
            "candidate": {"samples_ns_per_call": [sample, sample + 1, sample + 2]}
        },
    }


def _workflow(profile):
    return profile.Workflow(
        "example",
        "a complete public call",
        lambda: None,
        lambda: None,
        lambda: None,
    )


def test_persistent_same_reference_failure_survives_every_confirmation(
    profile, monkeypatch
):
    workflow = _workflow(profile)
    initial = {"example": _row(["numpy", "previous"], 100)}
    original = deepcopy(initial)
    measured = [_row(["numpy"], 200), _row(["numpy", "previous"], 300)]
    calls = []

    def measure(actual, rounds, relative_margin, absolute_margin_ns):
        calls.append((actual, rounds, relative_margin, absolute_margin_ns))
        return measured[len(calls) - 1]

    monkeypatch.setattr(profile, "_measure", measure)
    traces, persistent = profile._confirm_regressions(
        [workflow], initial, rechecks=2, relative_margin=0.15, absolute_margin_ns=300
    )

    assert persistent == {"example": ["numpy"]}
    assert calls == [(workflow, 21, 0.15, 300), (workflow, 21, 0.15, 300)]
    assert initial == original
    assert [attempt["measurement"] for attempt in traces["example"]] == measured
    assert traces["example"][0]["persistent_references_after"] == ["numpy"]
    assert traces["example"][1]["persistent_references_after"] == ["numpy"]


def test_cleared_second_confirmation_keeps_initial_and_both_raw_attempts(
    profile, monkeypatch
):
    workflow = _workflow(profile)
    initial = {"example": _row(["numpy"], 100)}
    original = deepcopy(initial)
    measured = [_row(["numpy"], 200), _row([], 50)]
    calls = []

    def measure(*args):
        calls.append(args)
        return measured[len(calls) - 1]

    monkeypatch.setattr(profile, "_measure", measure)
    traces, persistent = profile._confirm_regressions(
        [workflow], initial, rechecks=2, relative_margin=0.15, absolute_margin_ns=300
    )

    assert persistent == {}
    assert initial == original
    assert [attempt["measurement"] for attempt in traces["example"]] == measured
    assert traces["example"][0]["measurement"]["lanes"]["candidate"][
        "samples_ns_per_call"
    ] == [200, 201, 202]
    assert traces["example"][1]["persistent_references_after"] == []


@pytest.mark.parametrize("fresh_failure", [[], ["previous"]])
def test_confirmation_stops_when_original_reference_clears(
    profile, monkeypatch, fresh_failure
):
    workflow = _workflow(profile)
    initial = {"example": _row(["numpy"], 100)}
    measured = _row(fresh_failure, 200)
    calls = []

    def measure(*args):
        calls.append(args)
        assert len(calls) == 1
        return measured

    monkeypatch.setattr(profile, "_measure", measure)
    traces, persistent = profile._confirm_regressions(
        [workflow], initial, rechecks=2, relative_margin=0.15, absolute_margin_ns=300
    )

    assert persistent == {}
    assert len(calls) == 1
    assert traces["example"][0]["measurement"] == measured
    assert (
        traces["example"][0]["measurement"]["material_regression_vs"] == fresh_failure
    )


@pytest.mark.parametrize(("rechecks", "initial_failures"), [(2, []), (0, ["numpy"])])
def test_passed_or_explicit_exploratory_cases_do_not_trigger_measurements(
    profile, monkeypatch, rechecks, initial_failures
):
    workflow = _workflow(profile)
    initial = {"example": _row(initial_failures, 100)}

    def unexpected_measure(*_args):
        pytest.fail("an unrequested confirmation ran")

    monkeypatch.setattr(profile, "_measure", unexpected_measure)
    traces, persistent = profile._confirm_regressions(
        [workflow],
        initial,
        rechecks=rechecks,
        relative_margin=0.15,
        absolute_margin_ns=300,
    )

    assert traces == {}
    assert persistent == ({"example": ["numpy"]} if initial_failures else {})
