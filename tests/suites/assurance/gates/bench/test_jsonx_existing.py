"""Existing JSONX comparisons must audit outputs and isolate saved sources.

These gates run fixture oracles or simulated workers. No throughput threshold,
calibrated timer, or Python profiler runs in ordinary correctness execution.
"""

from __future__ import annotations

from argparse import Namespace
import copy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.profiles import jsonx_existing as profile


def _existing_cases():
    for backend in ("orjson", "msgspec", "pybase64"):
        pytest.importorskip(backend)
    from bench.core.cases import registry
    from bench.profiles.serde_profile import _workloads
    from bench.workloads.speed_cases_codecs import _serde_cases

    return (
        [case for case in _serde_cases() if ".jsonx." in case.name],
        [case for case in registry(("serde",)) if case.name.startswith("jsonx.")],
        _workloads(),
    )


def test_smoke_audits_all_existing_fixtures_without_timing_or_profiling(monkeypatch):
    _existing_cases()
    from bench import speeds
    from bench.core import report
    from bench.profiles import serde_profile
    from unstd.serde import jsonx

    def forbidden(*_args, **_kwargs):
        pytest.fail("smoke must never run a calibrated timer or profiler")

    monkeypatch.setattr(speeds, "measure", forbidden)
    monkeypatch.setattr(report, "measure", forbidden)
    monkeypatch.setattr(serde_profile, "measure", forbidden)
    monkeypatch.setattr(profile.cProfile, "Profile", forbidden)
    # Native compilation is not a prerequisite for this correctness gate.
    # Import/source/native provenance has independent rejection checks below.
    monkeypatch.setattr(profile, "_provenance", lambda *_args: {"source": "checked"})
    harness = Path(profile.__file__).resolve().parents[2]
    source = Path(jsonx.__file__).resolve().parents[2]
    result = profile._worker(source, 1, 1, smoke=True, harness=harness)
    assert len(result["public_case_names"]) == 9
    assert len(result["core_case_names"]) == 7
    assert "jsonx.dumps/records" in result["existing_profile_case_names"]
    assert "unstd.serde.jsonx.canonical_strict" in result["public_case_names"]
    assert "existing_serde_profile" not in result
    assert "public_registry" not in result


@pytest.mark.parametrize("kind", ["public", "core", "profile"])
def test_corrupted_existing_oracles_are_rejected(kind):
    from bench.speeds import RegistryError

    public, core, workloads = _existing_cases()
    if kind == "public":
        public[0] = replace(public[0], run=lambda: "changed JSON bytes")
        error = RegistryError
        message = "correctness audit failed"
    elif kind == "core":
        core[0] = replace(core[0], accel=lambda _value: "changed JSON bytes")
        error = AssertionError
        message = "core correctness audit failed"
    else:
        workloads[0] = replace(workloads[0], call=lambda: "changed JSON bytes")
        error = AssertionError
        message = "profile correctness audit failed"
    with pytest.raises(error, match=message):
        profile._audit_existing(public, core, workloads)


@pytest.mark.parametrize("layout", ["native-serde", "native/serde"])
def test_provenance_hashes_actual_native_filenames_and_rejects_missing_closure(
    tmp_path, layout
):
    source = tmp_path / "src"
    package = source / "unstd/serde"
    package.mkdir(parents=True)
    python = package / "jsonx.py"
    python.write_text("# saved product\n")
    binary = package / "_json_native.test.so"
    binary.write_bytes(b"saved native binary")
    native = tmp_path / layout / "src"
    native.mkdir(parents=True)
    adapter = native / "adapter.c"
    adapter.write_text('#include "identity.h"\n')
    header = native / "identity.h"
    header.write_text("/* saved native helper */\n")
    jsonx = SimpleNamespace(
        __file__=str(python), _native=SimpleNamespace(__file__=binary)
    )
    result = profile._provenance(source, jsonx)
    assert result["native_source_sha256"] == {
        "src/adapter.c": sha256(adapter.read_bytes()).hexdigest(),
        "src/identity.h": sha256(header.read_bytes()).hexdigest(),
    }
    assert (
        result["python_sha256"]["unstd/serde/_json_native.test.so"]
        == sha256(binary.read_bytes()).hexdigest()
    )
    adapter.unlink()
    header.unlink()
    with pytest.raises(RuntimeError, match="native C/header sources are missing"):
        profile._provenance(source, jsonx)


def test_wrong_product_native_and_harness_imports_are_rejected(tmp_path):
    jsonx = SimpleNamespace(__file__=str(tmp_path / "elsewhere/jsonx.py"))
    with pytest.raises(RuntimeError, match=r"requested .* imported"):
        profile._provenance(tmp_path / "src", jsonx)
    jsonx.__file__ = str(tmp_path / "src/unstd/serde/jsonx.py")
    jsonx._native = SimpleNamespace(__file__=str(tmp_path / "elsewhere/native.so"))
    with pytest.raises(RuntimeError, match="requested native extension"):
        profile._provenance(tmp_path / "src", jsonx)
    with pytest.raises(RuntimeError, match=r"requested harness .* imported"):
        profile._check_harness(
            tmp_path, [test_wrong_product_native_and_harness_imports_are_rejected]
        )


def test_smoke_rotates_three_parameterized_sources_and_retains_worker_evidence(
    tmp_path, monkeypatch
):
    harness = tmp_path / "harness"
    for name in profile._FIXTURE_FILES:
        path = harness / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# unchanged fixture\n")
    sources = [tmp_path / label / "src" for label in profile._LABELS]
    output = tmp_path / "report.json"
    args = Namespace(
        original=sources[0],
        first_pass=sources[1],
        candidate=sources[2],
        harness=harness,
        rounds=1,
        profile_rounds=1,
        repetitions=3,
        smoke=True,
        output=output,
    )
    observed = []

    def worker(command, *, cwd, env, **_kwargs):
        source = command[command.index("--source") + 1]
        observed.append(source)
        assert command[0] == profile.sys.executable
        assert command[1] == str(Path(profile.__file__).resolve())
        assert "--worker" in command
        assert "--smoke" in command
        assert cwd == harness
        assert env["PYTHONPATH"] == profile.os.pathsep.join((source, str(harness)))
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        result = {
            "environment": {"same": True},
            "harness_sha256": profile._harness_hashes(harness),
            "provenance": {"source": source},
            "public_case_names": ["public"],
            "core_case_names": ["core"],
            "existing_profile_case_names": ["profile"],
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr(profile.subprocess, "run", worker)
    result = profile._compare(args)
    assert observed == [str(sources[index]) for index in (0, 1, 2, 1, 2, 0, 2, 0, 1)]
    assert result["complete"]
    assert len(result["runs"]) == 9
    assert json.loads(output.read_text())["runs"] == result["runs"]
    assert "public_registry_comparisons" not in result


def test_wall_regressions_cannot_be_hidden_by_fast_cpu_diagnostics():
    runs = []
    for label, wall, cpu in (
        ("original", 100.0, 95.0),
        ("first_pass", 80.0, 75.0),
        ("candidate", 200.0, 1.0),
    ):
        runs.append(
            {
                "label": label,
                "public_registry": {
                    "jsonx.dumps": {
                        "measurement": {"per_op_ns": wall, "cpu_samples_ns": [cpu]}
                    }
                },
            }
        )
    result = profile._comparisons(runs, "public_registry")["jsonx.dumps"]
    assert result["final_over_original_speedup"] == 0.5
    assert result["final_over_first_pass_speedup"] == 0.4


def test_backend_fingerprint_observes_actual_code_and_native_bytes(
    tmp_path, monkeypatch
):
    wrapper = tmp_path / "backend.py"
    binary = tmp_path / "backend.so"
    wrapper.write_text("# provider wrapper\n")
    binary.write_bytes(b"original native provider")
    installed = SimpleNamespace(
        version="1.0",
        files=[Path("backend.py"), Path("backend.so")],
        locate_file=lambda path: tmp_path / path,
    )
    monkeypatch.setattr(profile, "distribution", lambda _name: installed)
    before = profile._backend_fingerprint("provider")
    assert before["code_files"] == 2
    assert before["native_sha256"] == {
        "backend.so": sha256(binary.read_bytes()).hexdigest()
    }
    binary.write_bytes(b"replaced native provider")
    after = profile._backend_fingerprint("provider")
    assert before["version"] == after["version"]
    assert before["code_sha256"] != after["code_sha256"]
    wrapper.write_text("# changed wrapper\n")
    assert (
        profile._backend_fingerprint("provider")["code_sha256"] != after["code_sha256"]
    )


@pytest.mark.parametrize("field", ["environment", "harness_sha256", "provenance"])
def test_source_or_environment_drift_cannot_support_a_comparison(field):
    row = {
        "label": "original",
        "environment": {"same": True},
        "harness_sha256": {"same": "hash"},
        "provenance": {"source": "same"},
        "public_case_names": ["public"],
        "core_case_names": ["core"],
        "existing_profile_case_names": ["profile"],
    }
    changed = copy.deepcopy(row)
    changed[field] = {"changed": True}
    with pytest.raises(RuntimeError, match=r"differ|source changed"):
        profile._validate_runs([row, changed], smoke=True)


def test_timing_cli_rejects_undersized_rounds_and_missing_comparison_paths():
    with pytest.raises(SystemExit):
        profile._arguments(["--harness", "."])
    arguments = [
        "--harness",
        ".",
        "--original",
        "a",
        "--first-pass",
        "b",
        "--candidate",
        "c",
        "--output",
        "report.json",
        "--rounds",
        "1",
    ]
    with pytest.raises(SystemExit):
        profile._arguments(arguments)
    assert profile._arguments([*arguments, "--smoke"]).smoke
