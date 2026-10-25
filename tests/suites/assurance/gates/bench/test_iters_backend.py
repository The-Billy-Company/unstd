"""Iterator evidence isolates sources and validates outputs around paired timing."""

from __future__ import annotations

from dataclasses import replace
import gc
import importlib.util
import json
import os
import py_compile
import shutil
import sys

import pytest

from tests.support.environment.anchors import REPO_ROOT


_SPEC = importlib.util.spec_from_file_location(
    "_iters_profile_gate", REPO_ROOT / "bench/profiles/iters_backend.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
profile = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = profile
_SPEC.loader.exec_module(profile)


def _copy_source(destination):
    shutil.copytree(
        profile._ROOT / "src" / "unstd" / "iters",
        destination / "unstd" / "iters",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return destination


def test_private_aliases_load_each_selected_tree_without_package_initializers(tmp_path):
    for label in ("before", "after"):
        package = tmp_path / label / "unstd" / "iters"
        package.mkdir(parents=True)
        for name in profile._MODULES:
            (package / f"{name}.py").write_text("__all__ = []\n")
        (package / "select.py").write_text(
            f"__all__ = ['first']\ndef first(values): return {label!r}\n"
        )
        (package / "__init__.py").write_text(
            "raise RuntimeError('package initialization must not run')\n"
        )
    before = profile._load(tmp_path / "before", "before_gate")
    after = profile._load(tmp_path / "after", "after_gate")
    assert before.first([0]) == "before"
    assert after.first([0]) == "after"
    assert before.first.__module__ != after.first.__module__


def test_selected_source_executes_despite_timestamp_valid_stale_bytecode(tmp_path):
    package = tmp_path / "unstd" / "iters"
    package.mkdir(parents=True)
    for name in profile._MODULES:
        (package / f"{name}.py").write_text("__all__ = []\n")
    target = package / "select.py"
    original = "__all__ = ['first']\ndef first(values): return 'before'\n"
    target.write_text(original)
    py_compile.compile(str(target), doraise=True)
    stamp = target.stat()
    target.write_text(original.replace("'before'", "'after!'"))
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    api = profile._load(tmp_path, "stale_gate")
    assert api.first([0]) == "after!"


@pytest.mark.parametrize("eager", [True, False])
def test_all_workload_oracles_run_without_timing(eager, monkeypatch):
    def forbidden(*_args):
        pytest.fail("output smoke checks must not run a timed batch")

    monkeypatch.setattr(profile, "_batch", forbidden)
    api = profile._load(profile._ROOT / "src", f"oracle_gate_{eager}")
    cases = profile._cases(api, eager_prefix=eager)
    assert len({case.name for case in cases}) == len(cases)
    for case in cases:
        case.check()
    by_name = {case.name: case for case in cases}
    assert len(by_name["composition/round-robin-take64"].run()) == 64
    assert (
        "materialization" in by_name["composition/round-robin-take64"].note
    ) == eager


@pytest.mark.parametrize("actual", [[1], (0,), 0])
def test_bad_values_or_output_shapes_fail_before_timing(actual, monkeypatch):
    def forbidden(*_args):
        pytest.fail("incorrect output reached the timing loop")

    monkeypatch.setattr(profile, "_batch", forbidden)
    good = profile.Case("fixture", lambda: [0], [0], "")
    bad = replace(good, run=lambda: actual)
    with pytest.raises(AssertionError, match="independent iterator oracle"):
        profile._paired(bad, good, 3)


def test_samples_alternate_adjacent_batches_with_one_common_iteration_count(
    monkeypatch,
):
    calls = []
    checks = []

    def before():
        checks.append("baseline")
        return [0]

    def after():
        checks.append("candidate")
        return [0]

    def batch(run, count):
        calls.append((run, count))
        return {"wall_ns": 5_000_000, "cpu_ns": 4_000_000}

    monkeypatch.setattr(profile, "_batch", batch)
    measured = profile._paired(
        profile.Case("fixture", before, [0], "old"),
        profile.Case("fixture", after, [0], "new"),
        3,
    )
    assert checks == ["baseline", "candidate", "baseline", "candidate"]
    assert [run for run, _ in calls] == [
        before,
        after,
        before,
        after,
        after,
        before,
        before,
        after,
    ]
    assert {count for _, count in calls} == {measured["iterations"]} == {1}
    assert [sample["order"] for sample in measured["samples"]] == [
        ["baseline", "candidate"],
        ["candidate", "baseline"],
        ["baseline", "candidate"],
    ]
    assert measured["samples"][0]["candidate"]["cpu_ns"] == 4_000_000
    assert measured["speedup"] == 1
    assert measured["oracle_passed"]


def test_output_is_rechecked_after_timing(monkeypatch):
    outputs = iter(([0], [1]))
    before = profile.Case("fixture", lambda: next(outputs), [0], "")
    after = profile.Case("fixture", lambda: [0], [0], "")
    monkeypatch.setattr(
        profile, "_batch", lambda *_args: {"wall_ns": 5_000_000, "cpu_ns": 1}
    )
    with pytest.raises(AssertionError, match="independent iterator oracle"):
        profile._paired(before, after, 3)


def test_timing_failure_restores_garbage_collection(monkeypatch):
    enabled = gc.isenabled()
    failure = RuntimeError("batch failed")

    def fail(*_args):
        raise failure

    monkeypatch.setattr(profile, "_batch", fail)
    case = profile.Case("fixture", lambda: [0], [0], "")
    with pytest.raises(RuntimeError) as raised:
        profile._paired(case, case, 3)
    assert raised.value is failure
    assert gc.isenabled() is enabled


def test_source_hashes_cover_every_product_module_and_reject_missing_tree(tmp_path):
    source = _copy_source(tmp_path / "saved")
    before = profile._source_hashes(source)
    assert {f"unstd/iters/{name}.py" for name in profile._MODULES} <= before.keys()
    target = source / "unstd" / "iters" / "distinct.py"
    target.write_text(target.read_text() + "\n# changed\n")
    after = profile._source_hashes(source)
    assert before["unstd/iters/distinct.py"] != after["unstd/iters/distinct.py"]
    assert before["unstd/iters/select.py"] == after["unstd/iters/select.py"]
    with pytest.raises(ValueError, match="all four unstd/iters modules"):
        profile._source_hashes(tmp_path / "missing")


def test_report_retains_provenance_and_rejects_sources_changed_during_run(
    tmp_path, monkeypatch
):
    source = _copy_source(tmp_path / "saved")
    output = tmp_path / "report.json"
    args = [
        "--compare-source",
        str(source),
        "--source",
        str(source),
        "--rounds",
        "3",
        "--output",
        str(output),
    ]
    monkeypatch.setattr(profile, "_paired", lambda *_args: {"oracle_passed": True})
    assert profile.main(args) == 0
    report = json.loads(output.read_text())
    assert report["sources"] == {"baseline": str(source), "candidate": str(source)}
    assert report["source_hashes"]["baseline"] == profile._source_hashes(source)
    assert len(report["harness_sha256"]) == 64
    assert len(report["environment"]["executable_sha256"]) == 64
    assert report["rounds"] == 3
    assert set(report["measurements"]) == {
        case.name for case in profile._cases(profile._load(source, "report_gate"))
    }

    def change_source(*_args):
        target = source / "unstd" / "iters" / "select.py"
        target.write_text(target.read_text() + "\n# changed during measurement\n")
        return {}

    monkeypatch.setattr(profile, "_paired", change_source)
    with pytest.raises(RuntimeError, match="source changed during measurement"):
        profile.main(args)
    assert json.loads(output.read_text()) == report


def test_cli_rejects_insufficient_rounds_before_loading_sources(tmp_path):
    with pytest.raises(SystemExit) as raised:
        profile.main(
            [
                "--compare-source",
                str(tmp_path),
                "--rounds",
                "2",
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
    assert raised.value.code == 2
