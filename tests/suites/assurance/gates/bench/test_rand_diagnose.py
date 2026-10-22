"""Controls diagnose noise without excusing any frozen-ceiling violation."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import gc
import json
from typing import TYPE_CHECKING, Any

import pytest

from bench import rand_diagnose as diagnosis
from bench import speeds


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _pairs(candidate_ns: int, control_ns: int) -> list[diagnosis.Pair]:
    return [
        diagnosis.Pair(
            repetition,
            ("candidate", "control")
            if repetition % 2 == 0
            else ("control", "candidate"),
            diagnosis.Batch(10, candidate_ns * 10, candidate_ns * 9),
            diagnosis.Batch(10, control_ns * 10, control_ns * 9),
        )
        for repetition in range(3)
    ]


def _frozen() -> dict[str, Any]:
    return {"measured_ns": 100.0, "ceiling_ns": 130.0}


def test_raw_batch_preserves_wall_and_thread_cpu_clocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall = iter((1_000, 1_700))
    cpu = iter((100, 500))
    monkeypatch.setattr(diagnosis.time, "perf_counter_ns", lambda: next(wall))
    monkeypatch.setattr(diagnosis.time, "thread_time_ns", lambda: next(cpu))
    calls = []
    sample = diagnosis._batch(lambda: calls.append(None), 3)
    assert sample == diagnosis.Batch(3, 700, 400)
    assert len(calls) == 3


def test_candidate_only_slowdown_remains_a_code_investigation() -> None:
    result = diagnosis.summarize(_pairs(150, 100), _frozen())
    assert result["classification"] == "candidate slower than control; investigate code"
    assert result["within_frozen_ceiling"] is False
    assert result["frozen_ceiling_ns"] == 130
    assert len(result["pairs"]) == 3


def test_equal_slow_controls_suggest_environment_but_do_not_pass() -> None:
    result = diagnosis.summarize(_pairs(160, 160), _frozen())
    assert result["classification"] == "reference also slow; environment suspect"
    assert result["within_frozen_ceiling"] is False
    assert result["candidate_best_wall_ns"] == 160
    assert result["frozen_ceiling_ns"] == 130


def test_raw_ceiling_and_not_control_ratio_determines_a_pass() -> None:
    assert (
        diagnosis.summarize(_pairs(130, 50), _frozen())["within_frozen_ceiling"] is True
    )
    assert (
        diagnosis.summarize(_pairs(131, 131), _frozen())["within_frozen_ceiling"]
        is False
    )


@pytest.mark.parametrize("samples", [[], _pairs(100, 100)[:2]])
def test_diagnosis_requires_multiple_pairs(samples: list[diagnosis.Pair]) -> None:
    with pytest.raises(speeds.RegistryError, match="three paired"):
        diagnosis.summarize(samples, _frozen())


def test_alternating_order_retains_every_raw_pair_and_restores_gc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "unstd.rand.crypto.bits"
    measured: list[str] = []
    checked: list[int] = []

    def candidate() -> int:
        return 17

    def control() -> int:
        return 17

    def batch(fn: Any, iterations: int) -> diagnosis.Batch:
        assert not gc.isenabled()
        measured.append("candidate" if fn is candidate else "control")
        assert fn() == 17
        assert iterations == diagnosis.ITERATIONS[name]
        return diagnosis.Batch(iterations, iterations * 100, iterations * 90)

    monkeypatch.setattr(diagnosis, "_batch", batch)
    prior = gc.isenabled()
    gc.enable()
    try:
        case = speeds.Case(name, candidate, checked.append, "virtual fixture")
        results = diagnosis.paired([case], {name: control}, repetitions=3)
        assert gc.isenabled()
    finally:
        if not prior:
            gc.disable()
    assert measured == [
        "candidate",
        "control",
        "control",
        "candidate",
        "candidate",
        "control",
    ]
    assert checked == [17, 17, 17, 17]
    assert len(results[name]) == 3


def test_failure_restores_callers_disabled_gc(monkeypatch: pytest.MonkeyPatch) -> None:
    name = "unstd.rand.crypto.bits"
    case = speeds.Case(name, lambda: None, speeds.expect(None), "virtual fixture")

    def failed(*args: object) -> diagnosis.Batch:
        message = "injected batch failure"
        raise RuntimeError(message)

    monkeypatch.setattr(diagnosis, "_batch", failed)
    prior = gc.isenabled()
    gc.disable()
    try:
        with pytest.raises(RuntimeError, match="injected batch"):
            diagnosis.paired([case], {name: case.run}, repetitions=3)
        assert not gc.isenabled()
    finally:
        if prior:
            gc.enable()


@pytest.mark.parametrize("repetitions", [0, 1, 2])
def test_invalid_repetitions_do_not_run_a_batch(repetitions: int) -> None:
    with pytest.raises(speeds.RegistryError, match="three repetitions"):
        diagnosis.paired([], {}, repetitions=repetitions)


def test_real_controls_match_original_oracles_without_mutating_caller_engines() -> None:
    pytest.importorskip("numpy")
    from bench import speed_cases_runtime
    from unstd.rand import draw

    before_state, before_engine = draw._py.getstate(), draw._gen
    with ExitStack() as stack:
        originals = {case.name: case for case in speed_cases_runtime._random(stack)}
        candidates_state, candidates_engine = draw._py.getstate(), draw._gen
        references, identities = diagnosis.controls()
        assert identities == {
            "bits_is_exact_secrets_primitive": True,
            "shuffle_is_exact_stdlib_method": True,
        }
        for name, control in references.items():
            originals[name].check(control())
        assert draw._py.getstate() == candidates_state
        assert draw._gen is candidates_engine
    assert draw._py.getstate() == before_state
    assert draw._gen is before_engine


@pytest.mark.parametrize(
    ("candidate_ns", "control_ns", "expected_exit"),
    [(100, 100, 0), (160, 160, 1), (160, 100, 1)],
)
def test_cli_retains_absolute_verdicts_without_modifying_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_ns: int,
    control_ns: int,
    expected_exit: int,
) -> None:
    path = tmp_path / "baseline.json"
    before = b'{"immutable": true}\n'
    path.write_bytes(before)
    output = tmp_path / "diagnosis.json"
    baseline = {
        "cases": {name: _frozen() for name in diagnosis.ITERATIONS},
        "environment": {"cpu": "virtual"},
    }
    candidates = [
        speeds.Case(name, lambda: None, speeds.expect(None), "virtual fixture")
        for name in diagnosis.ITERATIONS
    ]

    @contextmanager
    def registry() -> Iterator[list[speeds.Case]]:
        yield candidates

    monkeypatch.setattr(speeds, "registry", registry)
    monkeypatch.setattr(speeds, "load_baseline", lambda path: baseline)
    monkeypatch.setattr(speeds, "validate_baseline", lambda cases, baseline: None)
    monkeypatch.setattr(speeds, "environment", lambda: {"cpu": "virtual"})
    monkeypatch.setattr(diagnosis, "controls", lambda: ({}, {"fixture": True}))
    monkeypatch.setattr(diagnosis, "source_evidence", lambda: {"matches_head": True})
    monkeypatch.setattr(
        diagnosis,
        "paired",
        lambda *args, **kw: {
            name: _pairs(candidate_ns, control_ns) for name in diagnosis.ITERATIONS
        },
    )
    assert (
        diagnosis.main(["--baseline", str(path), "--output", str(output)])
        == expected_exit
    )
    assert path.read_bytes() == before
    report = json.loads(output.read_text())
    for row in report["cases"].values():
        assert row["frozen_ceiling_ns"] == 130
        assert row["within_frozen_ceiling"] is (expected_exit == 0)
        assert len(row["pairs"]) == 3


def test_original_diagnosis_workloads_cannot_disappear() -> None:
    with pytest.raises(speeds.RegistryError, match="workloads are missing"):
        diagnosis._select([])


def test_diagnosis_cannot_overwrite_baseline_through_output_or_a_hard_link(
    tmp_path: Path,
) -> None:
    path = tmp_path / "speeds.json"
    before = b'{"frozen": true}\n'
    path.write_bytes(before)
    hard_link = tmp_path / "alias.json"
    hard_link.hardlink_to(path)
    for output in (path, hard_link):
        assert diagnosis.main(["--output", str(output), "--baseline", str(path)]) == 2
        assert path.read_bytes() == before
