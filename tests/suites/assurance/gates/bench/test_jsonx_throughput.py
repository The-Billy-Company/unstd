"""Server qualification must reject missing kernels and retain wall losses."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.profiles import jsonx_throughput as profile


def test_native_provenance_accepts_installed_wheel_without_c_sources(
    tmp_path, monkeypatch
):
    package = tmp_path / "installed/unstd/serde"
    package.mkdir(parents=True)
    python = package / "jsonx.py"
    python.write_text("# installed Python source\n")
    binary = package / f"_json_native{profile.sysconfig.get_config_var('EXT_SUFFIX')}"
    binary.write_bytes(b"installed extension")
    native = SimpleNamespace(__file__=str(binary))
    jsonx = SimpleNamespace(
        __file__=str(python), _native=native, loads=json.loads, dumpb=profile._compact
    )
    monkeypatch.setattr(profile, "_native_checks", lambda _: {"probes": True})
    result = profile._provenance(package.parents[1], jsonx, require_native=True)
    assert result["native_module"] == str(binary)
    assert result["native_source_sha256"] == {}
    assert result["native_qualification"] == {"probes": True}
    assert f"unstd/serde/{binary.name}" in result["sha256"]


@pytest.mark.parametrize("location", [None, "other/native.so", "unstd/serde/wrong.so"])
def test_native_provenance_rejects_missing_wrong_root_or_wrong_abi(tmp_path, location):
    package = tmp_path / "unstd/serde"
    package.mkdir(parents=True)
    python = package / "jsonx.py"
    python.write_text("# product\n")
    native = SimpleNamespace(__file__=str(tmp_path / location)) if location else None
    jsonx = SimpleNamespace(__file__=str(python), _native=native)
    with pytest.raises(RuntimeError, match="requested native extension"):
        profile._provenance(tmp_path, jsonx, require_native=True)


def _fake_codecs(monkeypatch):
    native = SimpleNamespace(
        encode=profile._compact,
        dumpb=lambda value, *_: profile._compact(value),
        line=lambda value: profile._compact(value) + b"\n",
        loads_lines=lambda value: [json.loads(line) for line in value.splitlines()],
        is_bound=lambda _: True,
        dispatch_ready=lambda _: True,
    )
    return SimpleNamespace(
        _native=native,
        _HAVE_ORJSON=True,
        _HAVE_MSGSPEC=True,
        _native_dumpb=native.dumpb,
        _native_line=native.line,
        loads=json.loads,
        dumpb=profile._compact,
        dumps=json.dumps,
    )


def test_native_probes_reject_silent_kernel_fallback(monkeypatch):
    jsonx = _fake_codecs(monkeypatch)
    jsonx._native.loads_lines = lambda _: NotImplemented
    with pytest.raises(RuntimeError, match="native_ndjson_decode"):
        profile._native_checks(jsonx)


def test_native_probes_reject_corrupted_kernel_output(monkeypatch):
    jsonx = _fake_codecs(monkeypatch)
    jsonx._native.loads_lines = lambda _: []
    with pytest.raises(RuntimeError, match="native_ndjson_decode"):
        profile._native_checks(jsonx)


def test_native_probes_require_live_public_bindings(monkeypatch):
    jsonx = _fake_codecs(monkeypatch)
    jsonx._native_dumpb = lambda *_: b"fallback"
    with pytest.raises(RuntimeError, match="inactive public native binding"):
        profile._native_checks(jsonx)


def test_python314_dispatch_must_be_bound_and_ready(monkeypatch):
    jsonx = _fake_codecs(monkeypatch)
    monkeypatch.setattr(profile.sys, "version_info", (3, 14))
    jsonx._native.dispatch_ready = lambda _: False
    with pytest.raises(RuntimeError, match=r"3\.14 native dispatch is inactive"):
        profile._native_checks(jsonx)


def test_native_probes_require_native_module_and_optional_backends():
    with pytest.raises(RuntimeError, match="requires _json_native"):
        profile._native_checks(SimpleNamespace(_native=None))


def test_real_installed_native_probes():
    pytest.importorskip("orjson")
    pytest.importorskip("msgspec")
    pytest.importorskip("unstd.serde._json_native")
    from unstd.serde import jsonx

    result = profile._native_checks(jsonx)
    assert all(result["probes"].values())


@dataclass
class _Measurement:
    per_op_ns: float = 10.0
    samples_ns: tuple[float, ...] = (10.0, 11.0)
    cpu_samples_ns: tuple[float, ...] = (1.0, 2.0)


def test_worker_audits_outputs_and_retains_raw_wall_and_cpu_samples(monkeypatch):
    monkeypatch.setattr(profile, "_provenance", lambda *_a, **_k: {})
    monkeypatch.setattr(
        profile,
        "_workloads",
        lambda: [profile.Workload("records", lambda: {"x": 1}, lambda: {"x": 1}, 10)],
    )
    monkeypatch.setattr(profile, "measure", lambda *_a, **_k: _Measurement())
    monkeypatch.setattr(profile, "version", lambda _: "same")
    result = profile._worker(Path("unused"), 1, "records")
    row = result["timings"]["records"]
    assert row["identical_output"]
    assert row["candidate"]["samples_ns"] == (10.0, 11.0)
    assert row["candidate"]["cpu_samples_ns"] == (1.0, 2.0)


def test_worker_rejects_empty_selection_without_timing(monkeypatch):
    monkeypatch.setattr(profile, "_provenance", lambda *_a, **_k: {})
    monkeypatch.setattr(profile, "_workloads", lambda: [])
    with pytest.raises(ValueError, match="no workloads match"):
        profile._worker(Path("unused"), 1, "missing")


def _simulated_worker(command, **_kwargs):
    source = command[command.index("--source") + 1]
    wall = 20.0 if source.endswith("after") else 10.0
    return json.dumps(
        {
            "environment": {"same": True},
            "provenance": {"source": source},
            "timings": {
                "records": {
                    "candidate": {"per_op_ns": wall, "cpu_samples_ns": [0.1]},
                    "stdlib": {"per_op_ns": 100.0},
                    "candidate_mib_s": 1.0,
                }
            },
        }
    )


def test_comparison_alternates_installed_roots_and_saves_wall_regression(
    tmp_path, monkeypatch
):
    observed = []

    def worker(command, **kwargs):
        observed.append(command)
        return _simulated_worker(command, **kwargs)

    monkeypatch.setattr(profile.subprocess, "check_output", worker)
    output = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="wall-clock slowdown exceeds"):
        profile.main(
            [
                "--compare-source",
                str(tmp_path / "before"),
                "--source",
                str(tmp_path / "after"),
                "--require-native",
                "--max-slowdown",
                "1.15",
                "--rounds",
                "1",
                "--repetitions",
                "2",
                "--output",
                str(output),
            ]
        )
    assert [command[command.index("--source") + 1] for command in observed] == [
        str(tmp_path / name) for name in ("before", "after", "after", "before")
    ]
    assert all("--require-native" in command for command in observed)
    report = json.loads(output.read_text())
    assert report["regressions"] == ["records"]
    assert report["comparisons"]["records"]["before_over_after"] == 0.5
    assert len(report["runs"]) == 4


def test_comparison_rejects_product_drift_between_repetitions(tmp_path, monkeypatch):
    calls = 0

    def worker(command, **kwargs):
        nonlocal calls
        calls += 1
        result = json.loads(_simulated_worker(command, **kwargs))
        result["provenance"]["hash"] = calls
        return json.dumps(result)

    monkeypatch.setattr(profile.subprocess, "check_output", worker)
    with pytest.raises(RuntimeError, match="product changed between repetitions"):
        profile.main(["--source", str(tmp_path), "--repetitions", "2"])


@pytest.mark.parametrize("limit", ["nan", "inf", "0.9"])
def test_slowdown_limit_requires_finite_valid_ratio(limit):
    with pytest.raises(SystemExit):
        profile.main(["--max-slowdown", limit])


def test_slowdown_gate_requires_comparison_and_saved_evidence():
    with pytest.raises(SystemExit):
        profile.main(["--max-slowdown", "1.15", "--output", "result.json"])
    with pytest.raises(SystemExit):
        profile.main(["--max-slowdown", "1.15", "--compare-source", "before"])
