"""Consumer-shaped clone evidence rejects alias and model-state shortcuts."""

from __future__ import annotations

import copy
import dataclasses
import json
import sys
from types import ModuleType
from typing import TYPE_CHECKING

import pytest


pytest.importorskip("pydantic")

from bench.diagnostics.clone_allocations import _record_controls
from bench.profiles import clone_common_cpu as profile
from bench.profiles import clone_cpu as base
from bench.workloads.clone_consumers import Attachment, Question, workloads
from unstd import clone
from unstd.clone import structural


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.mark.parametrize("name", [case.name for case in workloads()])
def test_consumer_shapes_retain_public_and_stdlib_contracts(name: str) -> None:
    case = next(case for case in workloads() if case.name == name)
    result = getattr(clone, case.operation)(case.source)
    reference = dataclasses.asdict if case.operation == "asdict" else copy.deepcopy
    assert profile._check(case, "current", result)
    assert profile._check(case, "stdlib", reference(case.source))


def test_consumer_models_reject_shallow_state_copies() -> None:
    case = next(
        case for case in workloads() if case.name == "consumer-question-private-alias"
    )
    assert isinstance(case.source, Question)
    assert not case.check(case.source.model_copy(), False)
    wrong = clone.deep(case.source)
    wrong._publication["rows"] = copy.deepcopy(wrong._publication["rows"])
    assert not case.check(wrong, False)


def test_consumer_models_reject_equal_fields_with_a_different_record_type() -> None:
    class ForeignQuestion(Question):
        pass

    case = next(case for case in workloads() if case.name == "consumer-question-tiny")
    assert isinstance(case.source, Question)
    foreign = ForeignQuestion.model_validate(case.source.model_dump())
    assert foreign.model_dump() == case.source.model_dump()
    assert not case.check(foreign, False)


def test_consumer_asdict_requires_separate_positional_copies() -> None:
    case = next(
        case for case in workloads() if case.name == "consumer-asdict-field-alias"
    )
    result = clone.asdict(case.source)
    assert result["left"] is not result["right"]
    result["right"] = result["left"]
    assert not case.check(result, False)


def test_consumer_foreign_hooks_keep_aliases_with_their_structural_sibling() -> None:
    case = next(case for case in workloads() if case.name == "consumer-copy-hook-alias")
    result = clone.deep(case.source)
    assert isinstance(result, dict)
    foreign = result["foreign"]
    assert isinstance(foreign, dict)
    assert foreign["copied"] is result["values"]
    foreign["copied"] = copy.deepcopy(foreign["copied"])
    assert not case.check(result, False)


def test_consumer_cycle_checker_rejects_source_backreferences() -> None:
    case = next(case for case in workloads() if case.name == "consumer-builtin-cycle")
    assert isinstance(case.source, dict)
    wrong = dict(case.source)
    assert not case.check(wrong, False)
    correct = clone.deep(case.source)
    assert correct["cycle"] is correct
    assert profile._check(case, "current", correct)


def test_model_node_counts_include_record_state_and_nested_models() -> None:
    cases = {case.name: case for case in workloads()}
    tiny = profile._node_counts(cases["consumer-question-tiny"].source)
    assert tiny["records"] == 1
    assert tiny["dicts"] >= 2
    source = cases["consumer-attachment-images-10"].source
    assert isinstance(source, Attachment)
    assert profile._node_counts(source)["records"] == 11


def test_consumer_fixtures_do_not_change_the_original_common_workload_set() -> None:
    original = profile._workloads()
    consumer = workloads()
    assert not {case.name for case in original} & {case.name for case in consumer}
    assert len({case.name for case in consumer}) == len(consumer)


def test_consumer_profile_warms_every_public_implementation_before_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = structural.deep
    models: list[Question] = []

    def deep(value: object) -> object:
        if isinstance(value, Question):
            models.append(value)
        return original(value)

    def snapshot(
        _directory: Path, _label: str, *, infer_native_bindings: bool
    ) -> ModuleType:
        return structural

    def timings(
        _calls: dict[str, Callable[[], object]], _rounds: int, _gc_enabled: bool
    ) -> dict[str, object]:
        # This test exercises preparation metadata and performs no timed work.
        return {}

    def no_profile(_call: Callable[[], object]) -> list[object]:
        return []

    monkeypatch.setattr(structural, "deep", deep)
    monkeypatch.setattr(profile, "_snapshot", snapshot)
    monkeypatch.setattr(base, "_timings", timings)
    monkeypatch.setattr(base, "_profile", no_profile)
    output = tmp_path / "warmups.json"
    profile.main(
        [
            "--compare-dir",
            str(tmp_path),
            "--original-dir",
            str(tmp_path),
            "--consumer-cases",
            "--include",
            "consumer-copy-hook-alias",
            "--output",
            str(output),
        ]
    )
    data = json.loads(output.read_text())["model_warmup"]
    assert data["labels"] == ["current", "before", "original", "stdlib"]
    assert len(models) == 3
    assert all(model is models[0] for model in models)
    for label in ("current", "before", "original"):
        assert data["implementations"][label]["checked"] is True
        assert data["implementations"][label]["marker_initialized_after"] is True
    assert data["implementations"]["stdlib"]["native_model_capability"] is None


def test_public_model_warmup_rejects_a_shallow_alias_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = structural.deep

    def shallow(value: object) -> object:
        return value.model_copy() if isinstance(value, Question) else original(value)

    def snapshot(
        _directory: Path, _label: str, *, infer_native_bindings: bool
    ) -> ModuleType:
        return structural

    monkeypatch.setattr(structural, "deep", shallow)
    monkeypatch.setattr(profile, "_snapshot", snapshot)
    with pytest.raises(AssertionError, match="incorrect public model warmup: current"):
        profile.main(["--compare-dir", str(tmp_path), "--consumer-cases"])


@pytest.mark.parametrize("value", ["0.99", "nan", "inf", "-inf"])
def test_stdlib_target_rejects_invalid_thresholds(tmp_path: Path, value: str) -> None:
    with pytest.raises(SystemExit) as failure:
        profile.main(["--compare-dir", str(tmp_path), f"--target-speedup={value}"])
    assert failure.value.code == 2


def test_target_enforcement_requires_an_explicit_speed_target(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as failure:
        profile.main(["--compare-dir", str(tmp_path), "--enforce-target"])
    assert failure.value.code == 2


@pytest.mark.parametrize(
    ("cpu", "wall", "met"),
    [(100.0, 100.0, True), (100.1, 100.0, False), (100.0, 100.1, False)],
)
def test_stdlib_target_requires_both_matched_clocks(
    cpu: float, wall: float, met: bool
) -> None:
    timings = {
        "current": {
            "median_cpu_ns": cpu,
            "median_wall_ns": wall,
            "samples": [{"cpu_ns": cpu, "wall_ns": wall}] * 3,
        },
        "stdlib": {
            "median_cpu_ns": 10000.0,
            "median_wall_ns": 10000.0,
            "samples": [{"cpu_ns": 10000.0, "wall_ns": 10000.0}] * 3,
        },
    }
    result = profile._target_result(timings, 100)
    assert result["met"] is met
    assert result["cpu_target_ns"] == 100
    assert result["wall_target_ns"] == 100


def test_record_allocation_controls_are_explicitly_not_valid_clones() -> None:
    case = next(
        case for case in workloads() if case.name == "consumer-question-private-alias"
    )
    kinds, controls = _record_controls(case.source)
    assert kinds == (Question,)
    raw = controls["exact_record_new_control"]()
    states = controls["shallow_record_state_control"]()
    assert isinstance(raw, list)
    assert isinstance(states, list)
    assert len(raw) == len(states) == 1
    assert type(raw[0]) is Question
    assert type(states[0]) is Question
    assert isinstance(case.source, Question)
    assert states[0].__dict__ is not case.source.__dict__
    assert states[0].args is case.source.args
    assert not case.check(states[0], False)


def test_snapshot_without_a_native_backend_disables_model_continuation(
    tmp_path: Path,
) -> None:
    (tmp_path / "structural.py").write_text(
        "_native_clone = None\n_NATIVE_MODEL_CONTINUATION = True\n"
    )
    snapshot = base._snapshot(tmp_path, "consumer_missing_backend")
    assert type(snapshot._NATIVE_MODEL_CONTINUATION) is int
    assert snapshot._NATIVE_MODEL_CONTINUATION == 0


@pytest.mark.parametrize("capability", [0, 1, True])
def test_snapshot_inference_uses_its_own_model_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: int
) -> None:
    @dataclasses.dataclass
    class Snapshot:
        _native_clone: Callable[[object], object]
        _NATIVE_MODEL_CONTINUATION: int

    def native(value: object) -> object:
        return value

    native.__module__ = "_clone_consumer_capability_backend"
    backend = ModuleType(native.__module__)
    backend.__dict__.update(clone_checked=native, MODEL_CONTINUATION=capability)
    monkeypatch.setitem(sys.modules, native.__module__, backend)
    wrapper = Snapshot(native, int(not capability))

    def snapshot(_directory: Path, _label: str) -> Snapshot:
        return wrapper

    monkeypatch.setattr(base, "_snapshot", snapshot)
    (tmp_path / "structural.py").write_text(
        "from unstd.clone._clone_native import clone_checked as _native_clone\n"
    )
    actual = profile._snapshot(tmp_path, "consumer_backend", infer_native_bindings=True)
    expected = capability if type(capability) is int else 0
    observed = actual._NATIVE_MODEL_CONTINUATION
    assert type(observed) is int
    assert observed == expected


def test_fused_model_route_proof_exercises_the_public_copier(
    native_mixed_graph,
) -> None:
    if not getattr(structural, "_NATIVE_MODEL_CONTINUATION", False):
        pytest.skip("native model continuation is not built")
    routes = profile._native_routes(structural, "current", require_models=True)
    assert routes["pydantic-model"] == ["_native_walk_graph"]


def test_fused_model_proof_requires_the_unmodified_model_handler(
    monkeypatch: pytest.MonkeyPatch, native_mixed_graph
) -> None:
    if not getattr(structural, "_NATIVE_MODEL_CONTINUATION", False):
        pytest.skip("native model continuation is not built")
    monkeypatch.setattr(structural, "_MODEL_WALK", object())
    with pytest.raises(RuntimeError, match="native model continuation unavailable"):
        profile._native_routes(structural, "changed_handler", require_models=True)


@pytest.mark.parametrize("feature", [0, True])
def test_fused_model_proof_requires_the_exact_integer_feature(
    monkeypatch: pytest.MonkeyPatch, feature: int, native_mixed_graph
) -> None:
    if not getattr(structural, "_NATIVE_MODEL_CONTINUATION", False):
        pytest.skip("native model continuation is not built")
    native = structural._native_walk_graph
    monkeypatch.setattr(structural, "_NATIVE_MODEL_CONTINUATION", feature)
    with pytest.raises(RuntimeError, match="native model continuation unavailable"):
        profile._native_routes(structural, "changed_feature", require_models=True)
    assert structural._native_walk_graph is native


def test_fused_model_proof_rejects_python_reconstruction_and_restores_profile(
    monkeypatch: pytest.MonkeyPatch, native_mixed_graph
) -> None:
    if not getattr(structural, "_NATIVE_MODEL_CONTINUATION", False):
        pytest.skip("native model continuation is not built")
    original = structural.deep

    def redundant(value: object) -> object:
        result = original(value)
        if isinstance(value, Question):
            structural._model(value, {})
        return result

    previous = sys.getprofile()
    monkeypatch.setattr(structural, "deep", redundant)
    with pytest.raises(RuntimeError, match="Python model reconstruction"):
        profile._native_routes(structural, "python_models", require_models=True)
    assert sys.getprofile() is previous
