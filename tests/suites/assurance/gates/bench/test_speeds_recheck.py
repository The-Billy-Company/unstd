"""Paired rechecks retain frozen limits, evidence and product isolation."""

from contextlib import contextmanager
import json
import os
import sys
from types import ModuleType

import pytest

from bench import speeds
from bench.core.timing import Measurement
from bench.profiles import speeds_recheck as recheck


@pytest.mark.parametrize(
    ("ratio", "ceiling", "failed"),
    [(1.0, 130, False), (1.4, 130, True), (1.06, 105, True)],
)
def test_decision_preserves_each_original_limit_under_common_drift(
    ratio, ceiling, failed
):
    pairs = [{"before_ns": old, "current_ns": old * ratio} for old in (160, 200, 180)]
    result = recheck.decision(pairs, {"measured_ns": 100, "ceiling_ns": ceiling})
    assert result["paired_limit"] == ceiling / 100
    assert result["failed"] is failed
    assert result["pairs"] == pairs


@pytest.mark.parametrize("invalid", [0, -1, float("nan"), float("inf"), True])
@pytest.mark.parametrize(
    "field", ["before_ns", "current_ns", "measured_ns", "ceiling_ns"]
)
def test_nonpositive_or_nonfinite_measurements_never_pass(invalid, field):
    pair, row = (
        {"before_ns": 100, "current_ns": 100},
        {"measured_ns": 100, "ceiling_ns": 130},
    )
    (pair if field in pair else row)[field] = invalid
    with pytest.raises(speeds.RegistryError, match="finite positive"):
        recheck.decision([pair], row)


def test_bound_namespace_preserves_deletions_and_restores_on_error(tmp_path):
    original, paths, environment = (
        recheck._namespace(),
        sys.path.copy(),
        os.environ.get("PYTHONPATH"),
    )
    old, deleted, added = (
        ModuleType("unstd"),
        ModuleType("unstd.deleted"),
        ModuleType("unstd.added"),
    )
    cohort = recheck.Cohort(
        tmp_path, {}, {"unstd": old, "unstd.deleted": deleted}, {}, {}
    )
    sentinel = RuntimeError("sentinel")

    def mutate():
        with recheck._bound(cohort):
            assert sys.modules["unstd"] is old
            del sys.modules["unstd.deleted"]
            sys.modules["unstd.added"] = added
            raise sentinel

    with pytest.raises(RuntimeError, match="sentinel"):
        mutate()
    assert recheck._namespace() == original
    assert sys.path == paths
    assert os.environ.get("PYTHONPATH") == environment
    assert "unstd.deleted" not in cohort.modules
    assert cohort.modules["unstd.added"] is added


def test_worker_rejects_workload_changes_before_loading_products():
    with pytest.raises(speeds.RegistryError, match="workload source"):
        recheck.worker(
            {
                "names": ["unstd.test.operation"],
                "baseline": {"cases": {}, "workload_sources": {}},
            }
        )


def test_product_drift_fails_before_any_timing(tmp_path):
    package = tmp_path / "unstd"
    package.mkdir()
    source = package / "__init__.py"
    source.write_text("original")
    cohort = recheck.Cohort(tmp_path, {}, {}, recheck._files(tmp_path), {})
    source.write_text("changed")
    with pytest.raises(speeds.RegistryError, match="source or native binary changed"):
        recheck._prove(cohort)


@pytest.mark.parametrize(
    ("ratio", "worker_error", "status"),
    [(1.0, False, 0), (1.4, False, 1), (1.0, True, 2)],
)
def test_cli_preserves_original_failure_all_retries_and_frozen_rows(
    tmp_path, monkeypatch, ratio, worker_error, status
):
    case = speeds.Case(
        "unstd.demo.operation", lambda: 1, speeds.expect(1), "one scalar"
    )
    measurement = Measurement(200, 10, 3, 0, (200, 200, 200), (200, 200, 200))
    monkeypatch.setattr(speeds, "environment", lambda: {"python": "fixture"})
    monkeypatch.setattr(speeds, "provenance", lambda: {})
    baseline_path = tmp_path / "baseline.json"
    baseline = speeds.freeze(
        [case], {case.name: Measurement(100, 10, 3, 0)}, baseline_path
    )
    original = baseline_path.read_bytes()

    @contextmanager
    def registry(*_args, **_kwargs):
        yield [case]

    monkeypatch.setattr(speeds, "registry", registry)
    monkeypatch.setattr(speeds, "check_coverage", lambda *_a, **_k: {})
    monkeypatch.setattr(speeds, "timings", lambda *_a, **_k: {case.name: measurement})

    def paired(*_args, **_kwargs):
        if worker_error:
            message = "wrong native cohort"
            raise speeds.RegistryError(message)
        pairs = [{"before_ns": 160, "current_ns": 160 * ratio}] * 3
        return {
            "cases": {case.name: recheck.decision(pairs, baseline["cases"][case.name])},
            "products": {"before": {}, "current": {}},
        }

    monkeypatch.setattr(speeds, "paired_revision_recheck", paired)
    output = tmp_path / "current.json"
    assert (
        speeds.main(
            [
                "verify",
                "--revision-baseline",
                "--reference-report",
                str(tmp_path / "before.json"),
                "--baseline",
                str(baseline_path),
                "--output",
                str(output),
                "--rounds",
                "3",
            ]
        )
        == status
    )
    result = json.loads(output.read_text())
    row = result["cases"][case.name]
    assert row["ns"] == 200
    assert row["ceiling_ns"] == 130
    assert row["absolute_failed"] is True
    assert row["attempts_ns"] == [200, 200, 200]
    assert len(row["attempts"]) == 3
    assert row["failed"] is (status != 0)
    assert result["absolute_failed"] == [case.name]
    assert baseline_path.read_bytes() == original
    if worker_error:
        assert result["paired_error"] == "wrong native cohort"


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_pair_preserves_gc_policy_and_restores_caller_state(
    tmp_path, monkeypatch, enabled, fails
):
    from contextlib import nullcontext
    import gc

    saved = gc.isenabled()
    case = speeds.Case("one", lambda: 1, speeds.expect(1), "one")
    cohort = recheck.Cohort(tmp_path, {"one": case}, {}, {}, {})
    monkeypatch.setattr(recheck, "_bound", lambda _: nullcontext())
    collected = []
    monkeypatch.setattr(recheck.gc, "collect", lambda: collected.append(gc.isenabled()))
    sentinel = RuntimeError("paired batch")

    def measure(*_):
        assert not gc.isenabled()
        if fails:
            raise sentinel
        return []

    monkeypatch.setattr(recheck, "_pair_measured", measure)
    try:
        (gc.enable if enabled else gc.disable)()
        if fails:
            with pytest.raises(RuntimeError, match="paired batch"):
                recheck._pair({"before": cohort, "current": cohort}, "one", 3)
        else:
            assert recheck._pair({"before": cohort, "current": cohort}, "one", 3) == []
        assert gc.isenabled() is enabled
        assert collected == [enabled]
    finally:
        (gc.enable if saved else gc.disable)()


@pytest.mark.parametrize(
    ("pair", "row"),
    [
        (
            {"before_ns": 1e308, "current_ns": 1e-308},
            {"measured_ns": 1, "ceiling_ns": 1.3},
        ),
        (
            {"before_ns": 1, "current_ns": 1},
            {"measured_ns": 1e-308, "ceiling_ns": 1e308},
        ),
    ],
)
def test_derived_ratio_underflow_and_limit_overflow_fail(pair, row):
    with pytest.raises(speeds.RegistryError, match="finite positive"):
        recheck.decision([pair], row)
