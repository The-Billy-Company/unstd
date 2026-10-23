"""Frozen speeds must detect slow/wrong work and refuse incomplete evidence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from typing import TYPE_CHECKING, Any

import pytest

from bench import speeds
from bench.core.timing import Measurement


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _case(name: str = "unstd.demo.function") -> speeds.Case:
    return speeds.Case(
        name, lambda: [1, True], speeds.expect([1, True]), "two scalar results"
    )


def _measurement(ns: float = 100) -> Measurement:
    return Measurement(ns, 100, 7, 0.02)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(speeds, "environment", lambda: {"python": "fixture"})
    return tmp_path / "speeds.json"


def _freeze(store: Path) -> dict[str, Any]:
    case = _case()
    return speeds.freeze([case], {case.name: _measurement()}, store)


@pytest.mark.parametrize(
    ("actual", "expected"),
    [([True], [1]), ([1], [1.0]), ([1], (1,)), ({"a": 1, "b": 2}, {"b": 2, "a": 1})],
)
def test_oracles_pin_nested_types_and_dictionary_order(
    actual: object, expected: object
) -> None:
    with pytest.raises(speeds.RegistryError, match="expected"):
        speeds.expect(expected)(actual)


def test_registry_rejects_missing_description_duplicates_and_empty() -> None:
    for cases in ([], [_case(), _case()], [replace(_case(), note="")]):
        with pytest.raises(speeds.RegistryError):
            speeds.validate_cases(cases)


def test_wrong_outputs_fail_before_any_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never_measure(*args: object, **kwargs: object) -> Measurement:
        pytest.fail("a wrong result was timed")

    monkeypatch.setattr(speeds, "measure", never_measure)
    with pytest.raises(speeds.RegistryError, match="correctness audit failed"):
        speeds.timings([replace(_case(), run=lambda: None)])


def test_oracles_run_after_measurement_too(monkeypatch: pytest.MonkeyPatch) -> None:
    result = [1, True]
    case = replace(_case(), run=lambda: list(result))

    def corrupt(*args: object, **kwargs: object) -> Measurement:
        result.clear()
        return _measurement()

    monkeypatch.setattr(speeds, "measure", corrupt)
    with pytest.raises(speeds.RegistryError, match="correctness audit failed"):
        speeds.timings([case])


def test_slowdown_and_boundary_have_real_verdicts(store: Path) -> None:
    baseline = _freeze(store)
    case = _case()
    assert speeds.regressions({case.name: _measurement(130)}, baseline) == []
    assert speeds.regressions({case.name: _measurement(131)}, baseline) == [case.name]


def test_bounded_rechecks_reject_persistent_slowdowns(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _freeze(store)
    slow = {_case().name: _measurement(1_000)}
    monkeypatch.setattr(speeds, "timings", lambda cases, **kw: slow)
    measured, history = speeds.recheck_regressions([_case()], slow, baseline)
    assert speeds.regressions(measured, baseline) == [_case().name]
    assert len(history[_case().name]) == 3


def test_rechecks_retain_transient_samples_without_relaxing_ceilings(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _freeze(store)
    slow = {_case().name: _measurement(1_000)}
    monkeypatch.setattr(
        speeds, "timings", lambda cases, **kw: {_case().name: _measurement()}
    )
    measured, history = speeds.recheck_regressions([_case()], slow, baseline)
    assert speeds.regressions(measured, baseline) == []
    assert [attempt.per_op_ns for attempt in history[_case().name]] == [1_000, 100]
    assert baseline["cases"][_case().name]["ceiling_ns"] == 130


def test_freeze_preserves_frozen_evidence_even_when_a_new_run_is_slower(
    store: Path,
) -> None:
    before = _freeze(store)
    original_bytes = store.read_bytes()
    case = _case()
    after = speeds.freeze([case], {case.name: _measurement(10_000)}, store, tolerance=1)
    assert after == before
    assert store.read_bytes() == original_bytes
    assert speeds.regressions({case.name: _measurement(10_000)}, after) == [case.name]


def test_partial_freeze_adds_cases_and_preserves_other_packages(store: Path) -> None:
    before = _freeze(store)
    other = _case("unstd.other.function")
    after = speeds.freeze([other], {other.name: _measurement(200)}, store)
    assert after["cases"][_case().name] == before["cases"][_case().name]
    assert after["cases"][other.name]["ceiling_ns"] == 260


@pytest.mark.parametrize("tolerance", [-0.01, 1.01, float("nan"), float("inf")])
def test_invalid_tolerance_is_never_persisted(store: Path, tolerance: float) -> None:
    with pytest.raises(speeds.RegistryError, match="tolerance"):
        speeds.freeze(
            [_case()], {_case().name: _measurement()}, store, tolerance=tolerance
        )
    assert not store.exists()


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "100"])
@pytest.mark.parametrize("field", ["measured_ns", "ceiling_ns"])
def test_malformed_speed_cannot_become_a_pass(
    store: Path, value: object, field: str
) -> None:
    raw = _freeze(store)
    raw["cases"][_case().name][field] = value
    store.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(speeds.RegistryError, match="invalid speed baseline"):
        speeds.load_baseline(store)


def test_missing_baseline_is_an_error(store: Path) -> None:
    with pytest.raises(speeds.RegistryError, match="invalid speed baseline"):
        speeds.load_baseline(store)


@pytest.mark.parametrize(
    "field", ["recorded", "tolerance", "environment", "workload_sources"]
)
def test_missing_document_metadata_is_a_clear_error(store: Path, field: str) -> None:
    raw = _freeze(store)
    del raw[field]
    store.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(speeds.RegistryError, match="invalid speed baseline"):
        speeds.load_baseline(store)


def test_source_edits_require_review_and_preserve_frozen_ceilings(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _freeze(store)
    monkeypatch.setattr(speeds, "workload_sources", lambda: {"edited.py": "a" * 64})
    with pytest.raises(speeds.RegistryError, match="workload source changed"):
        speeds.validate_baseline([_case()], before)
    after = speeds.freeze([_case()], {_case().name: _measurement(1_000)}, store)
    assert after["cases"] == before["cases"]
    assert after["workload_sources"] == {"edited.py": "a" * 64}
    assert speeds.regressions({_case().name: _measurement(1_000)}, after)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_current_measurements_never_pass(store: Path, value: float) -> None:
    baseline = _freeze(store)
    measured = {_case().name: _measurement(value)}
    with pytest.raises(speeds.RegistryError, match="invalid measurement"):
        speeds.regressions(measured, baseline)
    with pytest.raises(speeds.RegistryError, match="invalid measurement"):
        speeds.freeze([_case()], measured, store)


def test_unfrozen_missing_and_stale_cases_are_errors(store: Path) -> None:
    raw = _freeze(store)
    with pytest.raises(speeds.RegistryError, match="missing="):
        speeds.validate_baseline([_case(), _case("unstd.demo.added")], raw)
    with pytest.raises(speeds.RegistryError, match="stale="):
        speeds.validate_baseline([], raw)
    with pytest.raises(speeds.RegistryError, match="missing frozen speed"):
        speeds.regressions({"unstd.demo.added": _measurement()}, raw)


def test_revision_comparison_reports_additions_without_changing_shared_ceilings(
    store: Path,
) -> None:
    baseline = _freeze(store)
    original = store.read_bytes()
    added = _case("unstd.demo.added")
    shared, additions = speeds.revision_cases([_case(), added], baseline)
    assert [case.name for case in shared] == [_case().name]
    assert additions == [added.name]
    assert speeds.regressions({_case().name: _measurement(131)}, baseline) == [
        _case().name
    ]
    assert store.read_bytes() == original
    assert added.name not in baseline["cases"]


def test_revision_comparison_rejects_removed_or_changed_workloads(store: Path) -> None:
    baseline = _freeze(store)
    with pytest.raises(speeds.RegistryError, match="stale="):
        speeds.revision_cases([_case("unstd.demo.other")], baseline)
    with pytest.raises(speeds.RegistryError, match="changed workloads"):
        speeds.revision_cases([replace(_case(), note="changed")], baseline)


def test_revision_environment_records_native_changes_but_requires_same_runtime() -> (
    None
):
    before = {"python": "3.13", "native": {"clone": False}}
    current = {"python": "3.13", "native": {"clone": True}}
    assert not speeds.comparable_environment(before, current)
    assert speeds.comparable_environment(before, current, revision=True)
    assert not speeds.comparable_environment(
        before, {**current, "python": "3.14"}, revision=True
    )
    assert not speeds.comparable_environment(
        {**before, "cpu_affinity": [2]},
        {**current, "cpu_affinity": [3]},
        revision=True,
    )


def test_cpu_pinning_uses_allowed_cpu_and_restores_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changes: list[tuple[int, set[int]]] = []
    monkeypatch.setattr(
        speeds.os, "sched_getaffinity", lambda pid: {7, 3}, raising=False
    )
    monkeypatch.setattr(
        speeds.os,
        "sched_setaffinity",
        lambda pid, cpus: changes.append((pid, set(cpus))),
        raising=False,
    )

    def interrupted() -> None:
        with speeds._pin_cpu():
            assert changes == [(0, {3})]
            message = "fixture failure"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="fixture failure"):
        interrupted()
    assert changes == [(0, {3}), (0, {3, 7})]


def test_cpu_pinning_is_portable_when_affinity_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(speeds.os, "sched_getaffinity", raising=False)
    monkeypatch.delattr(speeds.os, "sched_setaffinity", raising=False)
    with speeds._pin_cpu():
        pass


def test_cpu_pinning_failure_is_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refused(pid: int, cpus: set[int]) -> None:
        message = "affinity refused"
        raise PermissionError(message)

    monkeypatch.setattr(speeds.os, "sched_getaffinity", lambda pid: {3}, raising=False)
    monkeypatch.setattr(speeds.os, "sched_setaffinity", refused, raising=False)
    with pytest.raises(PermissionError, match="affinity refused"), speeds._pin_cpu():
        pytest.fail("timing must not run after affinity failed")


def test_revision_cli_audits_additions_and_keeps_shared_slowdowns(
    store: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(store)
    shared = _case()
    added = _case("unstd.demo.added")

    @contextmanager
    def toy_registry() -> Iterator[list[speeds.Case]]:
        yield [shared, added]

    monkeypatch.setattr(speeds, "registry", toy_registry)
    monkeypatch.setattr(speeds, "check_coverage", lambda cases: {})
    monkeypatch.setattr(
        speeds,
        "measure",
        lambda run, **kw: _measurement(1_000 if run is shared.run else 200),
    )
    output = tmp_path / "report.json"
    assert (
        speeds.main(
            [
                "verify",
                "--revision-baseline",
                "--baseline",
                str(store),
                "--output",
                str(output),
            ]
        )
        == 1
    )
    report = json.loads(output.read_text())
    assert report["added"] == [added.name]
    assert report["cases"][added.name]["ceiling_ns"] is None
    assert report["failed"] == [_case().name]
    assert report["cases"][_case().name]["ceiling_ns"] == 130
    assert report["baseline_environment"] == {"python": "fixture"}
    added = replace(added, run=lambda: None)
    assert speeds.main(["verify", "--revision-baseline", "--baseline", str(store)]) == 2


def test_changing_a_workload_cannot_reuse_its_old_speed(store: Path) -> None:
    raw = _freeze(store)
    changed = replace(_case(), note="different payload")
    with pytest.raises(speeds.RegistryError, match="changed workloads"):
        speeds.validate_baseline([changed], raw)
    with pytest.raises(speeds.RegistryError, match="changed frozen workload"):
        speeds.freeze([changed], {changed.name: _measurement()}, store)


def test_different_environment_cannot_overwrite_a_baseline(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(store)
    before = store.read_bytes()
    monkeypatch.setattr(speeds, "environment", lambda: {"python": "different"})
    with pytest.raises(speeds.RegistryError, match="environment differs"):
        speeds.freeze([_case()], {_case().name: _measurement()}, store)
    assert store.read_bytes() == before


@pytest.mark.parametrize("rounds", [0, 1, 2])
def test_calibration_requires_multiple_rounds(rounds: int) -> None:
    with pytest.raises(speeds.RegistryError, match="three timing rounds"):
        speeds.timings([_case()], rounds=rounds)


def test_cli_reports_pass_fail_missing_and_environment_mismatch(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    @contextmanager
    def toy_registry() -> Iterator[list[speeds.Case]]:
        yield [_case()]

    monkeypatch.setattr(speeds, "registry", toy_registry)
    monkeypatch.setattr(speeds, "check_coverage", lambda cases: {})
    monkeypatch.setattr(
        speeds, "timings", lambda cases, **kw: {_case().name: _measurement()}
    )
    assert speeds.main(["verify", "--baseline", str(store)]) == 2
    assert "invalid speed baseline" in capsys.readouterr().err
    assert speeds.main(["freeze", "--baseline", str(store)]) == 0
    assert speeds.main(["verify", "--baseline", str(store)]) == 0
    monkeypatch.setattr(
        speeds, "timings", lambda cases, **kw: {_case().name: _measurement(1_000)}
    )
    assert speeds.main(["verify", "--baseline", str(store)]) == 1
    assert "1 regressions" in capsys.readouterr().out
    monkeypatch.setattr(speeds, "environment", lambda: {"python": "different"})
    assert speeds.main(["verify", "--baseline", str(store)]) == 2
    assert "environment differs" in capsys.readouterr().err
    assert speeds.main(["list", "--group", "missing"]) == 2


def test_inventory_finds_new_exports_own_methods_and_bound_codec_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    package = tmp_path / "unstd/demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        '__all__ = ["work", "Codec", "Error"]\n', encoding="utf-8"
    )

    class Codec:
        __module__ = "unstd.demo"
        __annotations__ = {"encode": "Callable[[object], bytes]"}

        def decode(self) -> None:
            pass

    module = SimpleNamespace(work=lambda: None, Codec=Codec, Error=ValueError)
    monkeypatch.setattr(speeds.importlib, "import_module", lambda name: module)
    assert speeds.public_apis(tmp_path / "unstd") == {
        "unstd.demo.work",
        "unstd.demo.Codec",
        "unstd.demo.Codec.decode",
        "unstd.demo.Codec.encode",
    }


def test_inventory_finds_annotated_and_conditional_public_aliases(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public.py"
    source.write_text(
        "alias: object = factory\nif True:\n    conditional = factory\n"
        "def direct(): pass\n_internal = factory\n",
        encoding="utf-8",
    )
    assert speeds._exports(source) == {"alias", "conditional", "direct"}


def test_unknown_api_ids_cannot_create_fake_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(speeds, "public_apis", lambda: set())
    with pytest.raises(
        speeds.RegistryError, match=r"unknown APIs=.*unstd.demo.function"
    ):
        speeds.check_coverage([_case()])
