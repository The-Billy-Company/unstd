"""Adjacent JSON batches retain independent products and reject timing drift."""

from __future__ import annotations

import importlib
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from bench.profiles import jsonx_throughput as profile


@pytest.mark.parametrize("repetition", [0, 1])
def test_batches_alternate_adjacent_revisions_with_equal_iteration_budgets(
    monkeypatch, repetition
):
    observed = []

    def operation(label):
        def call():
            return 1

        call.label = label
        return call

    cases = {
        label: profile.Workload("one", operation(label), operation("ref-" + label), 1)
        for label in ("before", "after")
    }

    def batch(function, iterations):
        assert iterations == 2
        observed.append(function.label)
        return 24 if function.label == "after" else 20

    monkeypatch.setattr(profile.timing, "_calibrate", lambda _: (2, 100))
    monkeypatch.setattr(profile.timing, "_batch", batch)
    clock = iter(range(100))
    monkeypatch.setattr(profile.time, "thread_time_ns", lambda: next(clock))
    measurements, pairs = profile._paired_measure(cases, 3, repetition)
    expected = []
    for index in range(3):
        order = ["before", "after"]
        if (index + repetition) % 2:
            order.reverse()
        expected.extend([*order, *("ref-" + label for label in order)])
    assert observed == expected
    assert [pair["after_over_before"] for pair in pairs] == [1.2] * 3
    for measured in measurements.values():
        for metric in ("candidate", "stdlib"):
            assert measured[metric]["iters"] == 2
            assert measured[metric]["rounds"] == 3
            assert len(measured[metric]["samples_ns"]) == 3
            assert len(measured[metric]["cpu_samples_ns"]) == 3


def _namespace():
    return {
        name: module
        for name, module in sys.modules.items()
        if name == "unstd" or name.startswith("unstd.")
    }


def _installed_copy(tmp_path, name):
    jsonx = importlib.import_module("unstd.serde.jsonx")
    package = Path(jsonx.__file__).resolve().parents[1]
    root = tmp_path / name
    (root / "unstd").mkdir(parents=True)
    shutil.copy2(package / "__init__.py", root / "unstd/__init__.py")
    shutil.copytree(
        package / "serde",
        root / "unstd/serde",
        ignore=shutil.ignore_patterns("__pycache__", "*.dSYM"),
    )
    return root


def test_two_retained_native_installations_keep_their_bindings_and_namespace(tmp_path):
    pytest.importorskip("orjson")
    pytest.importorskip("msgspec")
    pytest.importorskip("unstd.serde._json_native")
    roots = {label: _installed_copy(tmp_path, label) for label in ("before", "after")}
    namespace, paths = _namespace(), sys.path.copy()
    retained = {
        label: profile._retained_workloads(root, "loads/small/str", require_native=True)
        for label, root in roots.items()
    }
    assert _namespace() == namespace
    assert sys.path == paths
    before, after = retained["before"][0], retained["after"][0]
    assert before.loads.__globals__ is not after.loads.__globals__
    assert before._native is not after._native
    assert Path(before._native.__file__) != Path(after._native.__file__)
    for _ in range(3):
        for label in ("before", "after"):
            jsonx, cases, proof = retained[label]
            assert (
                profile._provenance(roots[label], jsonx, require_native=True) == proof
            )
            for case in cases.values():
                assert profile._same(case.call(), case.reference())
    for label, (jsonx, _cases, proof) in retained.items():
        assert profile._provenance(roots[label], jsonx, require_native=True) == proof
    assert _namespace() == namespace
    assert sys.path == paths


def test_retained_loader_restores_every_product_module_when_loading_fails(
    tmp_path, monkeypatch
):
    root = _installed_copy(tmp_path, "broken")
    namespace, paths = _namespace(), sys.path.copy()
    failure = RuntimeError("native provenance rejected")

    def reject(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(profile, "_provenance", reject)
    with pytest.raises(RuntimeError) as caught:
        profile._retained_workloads(root, "small", require_native=True)
    assert caught.value is failure
    assert _namespace() == namespace
    assert sys.path == paths


@pytest.mark.parametrize("when", ["loading", "timing"])
def test_pairing_rejects_native_or_source_drift_before_and_after_batches(
    tmp_path, monkeypatch, when
):
    state = {"drift": False}
    case = profile.Workload("one", lambda: 1, lambda: 1, 1)

    def load(source, *_args, **_kwargs):
        if source.name == "after" and when == "loading":
            state["drift"] = True
        return SimpleNamespace(), {"one": case}, {"stable": True}

    def measure(*_args):
        state["drift"] = True
        return {
            label: {"candidate": {"per_op_ns": 1}, "stdlib": {"per_op_ns": 1}}
            for label in ("before", "after")
        }, []

    monkeypatch.setattr(profile, "_retained_workloads", load)
    monkeypatch.setattr(
        profile, "_provenance", lambda *_a, **_k: {"stable": not state["drift"]}
    )
    monkeypatch.setattr(profile, "_paired_measure", measure)
    with pytest.raises(RuntimeError, match=f"changed during {when}"):
        profile._paired_worker(tmp_path / "before", tmp_path / "after", 1, "", 0)
