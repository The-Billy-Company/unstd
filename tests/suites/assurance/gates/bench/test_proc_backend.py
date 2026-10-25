"""Process performance evidence validates behavior and source selection first."""

from __future__ import annotations

import gc
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bench.profiles import proc_backend as profile


@pytest.mark.parametrize(
    "changed",
    [
        {"returncode": 1},
        {"stdout": "wrong"},
        {"stderr": "wrong"},
        {"argv": ("different",)},
        {"stdout": b"expected"},
    ],
)
def test_wrong_result_is_rejected_before_timing(changed):
    case = profile.Workload("fixture", ("child",), "expected")
    calls = []

    def run(*_args, **_kwargs):
        calls.append(True)
        values = {
            "argv": case.argv,
            "returncode": 0,
            "stdout": "expected",
            "stderr": "",
        }
        return SimpleNamespace(**(values | changed))

    with pytest.raises(RuntimeError, match="independent fixture"):
        profile._measure(run, case)
    assert calls == [True]


def test_failed_timed_operation_restores_gc_state():
    enabled = gc.isenabled()
    case = profile.Workload("fixture", ("child",))
    calls = 0

    def run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            message = "child launch failed"
            raise OSError(message)
        return SimpleNamespace(argv=case.argv, returncode=0, stdout="", stderr="")

    with pytest.raises(OSError, match="launch failed"):
        profile._measure(run, case)
    assert gc.isenabled() is enabled


def test_public_signature_change_prevents_comparison():
    with pytest.raises(RuntimeError, match="signatures changed"):
        profile._compare_signatures(
            {"signatures": {"run": "(argv)"}},
            {"signatures": {"run": "(argv, extra)"}},
        )


def test_source_fingerprint_includes_internal_backend(tmp_path):
    proc = tmp_path / "unstd" / "proc"
    internal = proc / "_internal"
    internal.mkdir(parents=True)
    (proc / "__init__.py").write_text("# public\n")
    helper = internal / "backend.py"
    helper.write_text("# original\n")
    before = profile._source_hashes(tmp_path)
    helper.write_text("# changed\n")
    after = profile._source_hashes(tmp_path)
    assert before["unstd/proc/__init__.py"] == after["unstd/proc/__init__.py"]
    assert (
        before["unstd/proc/_internal/backend.py"]
        != after["unstd/proc/_internal/backend.py"]
    )


def test_direct_script_help_needs_no_product_or_optional_dependencies(tmp_path):
    result = subprocess.run(  # noqa: S603 — fixed local script with no site hooks
        [sys.executable, "-S", str(Path(profile.__file__).resolve()), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--baseline-source" in result.stdout
    assert "--rounds" in result.stdout


@pytest.mark.skipif(shutil.which("true") is None, reason="POSIX true fixture")
def test_fresh_worker_uses_saved_proc_subtree_without_top_level_package(tmp_path):
    source = tmp_path / "source"
    destination = source / "unstd" / "proc"
    shutil.copytree(profile._ROOT / "src" / "unstd" / "proc", destination)
    result = subprocess.run(  # noqa: S603 — fixed local script and saved source fixture
        [
            sys.executable,
            "-S",
            str(Path(profile.__file__).resolve()),
            "--worker",
            "--source",
            str(source),
            "--case",
            "empty-executable",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["source"] == str(source.resolve())
    assert report["imported_source"] == str((destination / "__init__.py").resolve())
    assert report["source_hashes"] == profile._source_hashes(source)
    assert set(report["measurements"]) == {"empty-executable"}
