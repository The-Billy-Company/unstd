"""Filesystem evidence keeps both sources isolated and checks real work."""

from __future__ import annotations

from argparse import ArgumentTypeError
import os
from pathlib import Path
import py_compile
import subprocess
import sys

import pytest

from bench.profiles import fs_backend as profile
from unstd import fs


def _source(root, marker, *, legacy=False):
    package = root / "unstd" / "fs"
    package.mkdir(parents=True)
    if legacy:
        # Historical snapshots remain valid comparison inputs.
        implementation = package / "_internal"
        implementation.mkdir()
        (implementation / "__init__.py").write_text("", encoding="utf-8")
        traversal = implementation / "walk.py"
        module = "unstd.fs._internal.walk"
    else:
        traversal = package / "traversal.py"
        module = "unstd.fs.traversal"
    traversal.write_text(f"def marker():\n    return {marker!r}\n", encoding="utf-8")
    (package / "__init__.py").write_text(
        f"from {module} import marker\n"
        "__all__ = ['marker']\n_FD_WALK_SUPPORTED = True\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize("legacy", [False, True])
def test_saved_source_binds_its_own_internals_and_restores_live_imports(
    tmp_path, legacy
):
    live = {
        name: module
        for name, module in sys.modules.copy().items()
        if name == "unstd" or name.startswith("unstd.")
    }
    before, old_evidence = profile._load_source(
        _source(tmp_path / "old", "old", legacy=legacy)
    )
    current, new_evidence = profile._load_source(
        _source(tmp_path / "new", "new", legacy=legacy)
    )
    assert before.marker() == "old"
    assert current.marker() == "new"
    assert fs is sys.modules["unstd.fs"]
    assert {
        name: module
        for name, module in sys.modules.copy().items()
        if name == "unstd" or name.startswith("unstd.")
    } == live
    path = "unstd/fs/_internal/walk.py" if legacy else "unstd/fs/traversal.py"
    assert old_evidence["files_sha256"][path] != new_evidence["files_sha256"][path]
    profile._check_signatures({"before": old_evidence, "current": new_evidence})


def test_stale_bytecode_cannot_disagree_with_recorded_source_hash(tmp_path):
    source = _source(tmp_path, "old")
    path = source / "unstd" / "fs" / "traversal.py"
    py_compile.compile(str(path), doraise=True)
    info = path.stat()
    path.write_text("def marker():\n    return 'new'\n", encoding="utf-8")
    assert path.stat().st_size == info.st_size
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    finders = list(sys.meta_path)
    module, evidence = profile._load_source(source)
    assert module.marker() == "new"
    assert "unstd/fs/traversal.py" in evidence["files_sha256"]
    assert sys.meta_path == finders
    assert sys.modules["unstd.fs"] is fs


def test_failed_source_import_restores_live_imports(tmp_path):
    source = _source(tmp_path, "broken")
    (source / "unstd" / "fs" / "__init__.py").write_text(
        "raise RuntimeError('failed import')\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="failed import"):
        profile._load_source(source)
    assert sys.modules["unstd.fs"] is fs


def test_missing_source_is_rejected_before_loading(tmp_path):
    with pytest.raises(ValueError, match=r"unstd/fs/__init__\.py"):
        profile._load_source(tmp_path)
    assert sys.modules["unstd.fs"] is fs


@pytest.mark.parametrize("change", ["edit", "remove"])
def test_changed_sources_are_rejected_before_report_publication(tmp_path, change):
    source = _source(tmp_path, "before")
    _module, evidence = profile._load_source(source)
    profile._check_source_hashes({"current": evidence})
    path = source / "unstd" / "fs" / "traversal.py"
    if change == "edit":
        path.write_text("def marker():\n    return 'after'\n", encoding="utf-8")
    else:
        path.unlink()
    with pytest.raises(RuntimeError, match="source changed during measurement"):
        profile._check_source_hashes({"current": evidence})


def test_changed_public_signatures_are_rejected():
    with pytest.raises(RuntimeError, match="public fs signatures changed"):
        profile._check_signatures(
            {
                "before": {"public_signatures": {"walk_files": "(root)"}},
                "current": {"public_signatures": {"walk_files": "(root, option)"}},
            }
        )


def test_wrong_outputs_fail_before_either_timer_starts(monkeypatch):
    events = []

    def check(_label, result):
        assert result == "expected", "wrong output"

    def forbidden(*_args):
        events.append("timed")
        pytest.fail("invalid outputs entered timing")

    monkeypatch.setattr(profile, "_batch", forbidden)
    case = profile.Case(
        "wrong",
        {"current": lambda: "wrong", "before": lambda: "expected"},
        check,
        lambda _label: None,
        "invalid result",
    )
    with pytest.raises(AssertionError, match="wrong output"):
        profile._measure(case, rounds=1, repetitions=1)
    assert events == []


def test_paired_order_alternates_and_batch_denominators_count_every_operation(
    monkeypatch,
):
    order = []
    checks = []

    def batch(call, repetitions):
        label = call()
        order.append(label)
        cost = 10 if label == "current" else 20
        return {
            "wall_ns": cost * repetitions,
            "process_cpu_ns": cost * repetitions * 2,
            "operations": repetitions,
        }, label

    monkeypatch.setattr(profile, "_batch", batch)
    monkeypatch.setattr(profile, "_profile", lambda _call: [])
    case = profile.Case(
        "paired",
        {"current": lambda: "current", "before": lambda: "before"},
        lambda label, result: checks.append((label, result)),
        lambda _label: None,
        "paired synthetic timing",
        batch_size=2,
    )
    result = profile._measure(case, rounds=3, repetitions=3, minimum_ns=0)
    assert order == ["current", "before", "before", "current", "current", "before"]
    assert all(label == value for label, value in checks)
    assert len(checks) == 8
    assert result["before_over_current"] == {"wall_ns": 2, "process_cpu_ns": 2}
    assert result["median_per_operation_ns"]["current"]["wall_ns"] == 10
    assert all(sample["operations"] == 6 for sample in result["samples"]["current"])


def test_calibration_uses_one_shared_count_for_both_sources(monkeypatch):
    counts = []

    def batch(call, repetitions):
        label = call()
        counts.append((label, repetitions))
        cost = 10 if label == "current" else 20
        return {
            "wall_ns": cost * repetitions,
            "process_cpu_ns": cost * repetitions,
            "operations": repetitions,
        }, label

    monkeypatch.setattr(profile, "_batch", batch)
    monkeypatch.setattr(profile, "_profile", lambda _call: [])
    case = profile.Case(
        "calibrated",
        {"current": lambda: "current", "before": lambda: "before"},
        lambda _label, _result: None,
        lambda _label: None,
        "calibration",
    )
    result = profile._measure(case, rounds=1, repetitions=2, minimum_ns=100)
    assert counts == [("current", 2), ("before", 2), ("current", 10), ("before", 10)]
    assert result["calibration"]["current"]["operations"] == 2
    assert result["samples"]["current"][0]["operations"] == 10


def test_prepared_cases_check_path_results_and_can_repeat_without_fixture_drift(
    tmp_path,
):
    source = Path(fs.__file__).resolve().parents[2]
    module, _evidence = profile._load_source(source)
    cases = profile._cases(
        {"current": module, "before": module},
        tmp_path,
        flat_files=3,
        directories=2,
        files_per_directory=2,
        depth=3,
    )
    assert {case.name for case in cases} >= {
        "walk-flat",
        "walk-nested",
        "walk-empty-directories",
        "walk-deep",
        "walk-portable",
        "walk-early-close",
        "atomic-write-bytes-1m",
        "atomic-writer",
        "atomic-replace-pair",
        "ensure-dir-existing",
        "ensure-dir-create-remove",
        "sync-dir",
    }
    for case in cases:
        for _ in range(2):
            results = {}
            for label in ("current", "before"):
                case.setup(label)
                results[label] = case.calls[label]()
                case.check(label, results[label])
            assert results["current"] == results["before"]
            if (
                case.name.startswith("atomic-write")
                or case.name == "ensure-dir-create-remove"
            ):
                assert results["current"].parent == tmp_path / "operations"
    assert not (tmp_path / "current").exists()
    assert not (tmp_path / "before").exists()
    flat = next(case for case in cases if case.name == "walk-flat")
    with pytest.raises(AssertionError):
        flat.check("current", [str(tmp_path / "flat" / "file-0")])


@pytest.mark.parametrize("value", ["0", "-1"])
def test_zero_or_negative_timing_budgets_are_rejected(value):
    with pytest.raises(ArgumentTypeError, match="positive integer"):
        profile._positive(value)


def test_direct_help_needs_no_product_or_optional_dependency(tmp_path):
    result = subprocess.run(  # noqa: S603 — fixed interpreter and local source, no shell
        [sys.executable, "-S", str(Path(profile.__file__).resolve()), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--compare-source" in result.stdout
    assert "--repetitions" in result.stdout
