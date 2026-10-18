"""Independent schedule vectors, finite admission, and immutable journal gates."""

from __future__ import annotations

import json
import random

import pytest

from tests.support.chaos import (
    MAX_EVENTS,
    MAX_JOURNAL_BYTES,
    VERSION,
    ChaosJournal,
    journal_sink,
    ordered,
)


@pytest.mark.parametrize(
    ("seed", "namespace", "expected"),
    [
        (173, "gate.clock", (8, 0, 1, 3, 9, 5, 2, 4, 7, 6)),
        (0, "gate.clock", (1, 0, 6, 3, 2, 5, 8, 9, 7, 4)),
        (173, "gate.entropy", (0, 5, 9, 8, 2, 4, 3, 1, 6, 7)),
    ],
)
def test_version_one_schedule_vectors(seed, namespace, expected):
    assert VERSION == 1
    assert ordered(seed, namespace, tuple(range(10))) == expected


def test_schedule_preserves_population_multiplicity_and_global_rng_state():
    values = ["same", "different", "same", "other"]
    state = random.getstate()
    first = ordered(2**64 - 1, "population", values)
    assert random.getstate() == state
    assert values == ["same", "different", "same", "other"]
    assert sorted(first) == sorted(values)
    assert isinstance(first, tuple)
    assert ordered(2**64 - 1, "population", values) == first


def test_reviewed_seed_and_namespace_matrix_has_distinct_replay_schedules():
    population = tuple(range(16))
    schedules = {
        ordered(seed, namespace, population)
        for seed in range(128)
        for namespace in ("syscall", "clock", "stream", "process")
    }
    # A finite deterministic collision canary, not a statistical RNG-quality claim.
    assert len(schedules) == 512


@pytest.mark.parametrize("seed", [True, False, -1, 2**64, 1.0, "1", None])
def test_schedule_rejects_invalid_seed(seed):
    with pytest.raises(ValueError, match="unsigned 64-bit"):
        ordered(seed, "valid", (1, 2))


@pytest.mark.parametrize("namespace", ["", "a" * 257, 1, None])
def test_schedule_rejects_invalid_namespace(namespace):
    with pytest.raises(ValueError, match="namespace"):
        ordered(173, namespace, (1, 2))


@pytest.mark.parametrize("population", [(), [], range(3), None, "abc", [0] * 10001])
def test_schedule_rejects_unbounded_or_empty_population(population):
    with pytest.raises(ValueError, match="reviewed values"):
        ordered(173, "valid", population)


def test_maximum_reviewed_population_and_singleton_are_supported():
    assert ordered(0, "singleton", ["only"]) == ("only",)
    population = tuple(range(10000))
    assert sorted(ordered(0, "maximum", population)) == list(population)


def test_journal_has_exact_json_schema_and_defensive_value_snapshots():
    journal = ChaosJournal(173, "reviewed::scenario")
    value = {"sequence": ["fault", "recovery"], "errno": 5}
    journal.record("provider.failed", observation=value)
    value["sequence"].append("caller mutation")
    expected = {
        "version": 1,
        "seed": 173,
        "scenario": "reviewed::scenario",
        "events": [
            {
                "event": "provider.failed",
                "observation": {"sequence": ["fault", "recovery"], "errno": 5},
            }
        ],
    }
    assert journal.snapshot() == expected
    returned = journal.snapshot()
    returned["events"].clear()
    assert journal.snapshot() == expected
    assert json.loads(json.dumps(journal.snapshot(), allow_nan=False)) == expected


@pytest.mark.parametrize("seed", [True, -1, 2**64, "1", 1.0])
def test_journal_rejects_invalid_replay_seed(seed):
    with pytest.raises(ValueError, match="unsigned 64-bit"):
        ChaosJournal(seed, "scenario")


@pytest.mark.parametrize("scenario", ["", 1, None])
def test_journal_requires_scenario_identity(scenario):
    with pytest.raises(ValueError, match="identity"):
        ChaosJournal(173, scenario)


@pytest.mark.parametrize("event", ["", "a" * 129, 1, None])
def test_journal_rejects_invalid_event_without_mutation(event):
    journal = ChaosJournal(173, "scenario")
    before = journal.snapshot()
    with pytest.raises(ValueError, match="event"):
        journal.record(event)
    assert journal.snapshot() == before


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), object(), b"bytes"]
)
def test_journal_refuses_non_json_evidence_without_mutation(value):
    journal = ChaosJournal(173, "scenario")
    journal.record("control", good=True)
    before = journal.snapshot()
    with pytest.raises((TypeError, ValueError)):
        journal.record("invalid", value=value)
    assert journal.snapshot() == before


def test_journal_event_and_byte_budgets_reject_atomically():
    journal = ChaosJournal(173, "scenario")
    for index in range(MAX_EVENTS):
        journal.record("e", index=index)
    before = journal.snapshot()
    with pytest.raises(ValueError, match="budget"):
        journal.record("overflow")
    assert journal.snapshot() == before
    assert len(before["events"]) == MAX_EVENTS
    small = ChaosJournal(173, "scenario")
    small.record("control")
    before = small.snapshot()
    with pytest.raises(ValueError, match="budget"):
        small.record("overflow", raw="x" * MAX_JOURNAL_BYTES)
    assert small.snapshot() == before


def test_live_sink_publishes_each_record_atomically_and_preserves_last_good_event(
    tmp_path,
):
    journal = ChaosJournal(
        173, "scenario::live", sink=journal_sink(tmp_path, "scenario::live")
    )
    journal.record("planned", fault="entropy")
    paths = list(tmp_path.glob("*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text()) == journal.snapshot()
    journal.record("observed", errno=5)
    second = json.loads(paths[0].read_text())
    assert [event["event"] for event in second["events"]] == ["planned", "observed"]
    with pytest.raises(ValueError, match="JSON compliant"):
        journal.record("invalid", value=float("nan"))
    assert json.loads(paths[0].read_text()) == second
    assert not list(tmp_path.glob("*.tmp"))
