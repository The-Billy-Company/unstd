"""Server qualification must reject missing kernels and retain wall losses."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.profiles import jsonx_throughput as profile
from tests.support.environment.process import run_python


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


def test_native_provenance_records_dispatch_after_qualification_warms_it(
    tmp_path, monkeypatch
):
    package = tmp_path / "unstd/serde"
    package.mkdir(parents=True)
    python = package / "jsonx.py"
    python.write_text("# installed source\n")
    binary = package / f"_json_native{profile.sysconfig.get_config_var('EXT_SUFFIX')}"
    binary.write_bytes(b"native binary")
    state = {"ready": False}
    native = SimpleNamespace(
        __file__=str(binary),
        is_bound=lambda _: True,
        dispatch_ready=lambda _: state["ready"],
    )
    jsonx = SimpleNamespace(
        __file__=str(python), _native=native, loads=json.loads, dumpb=profile._compact
    )

    def qualify(_jsonx):
        state["ready"] = True
        return {"probes": True}

    monkeypatch.setattr(profile, "_native_checks", qualify)
    first = profile._provenance(tmp_path, jsonx, require_native=True)
    assert all(result["ready"] for result in first["dispatch"].values())
    assert profile._provenance(tmp_path, jsonx, require_native=True) == first


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


@pytest.mark.parametrize("label", ["floats-256x256", "varied-floats-256x128"])
def test_shared_matrix_case_has_an_equal_output_independent_control(label):
    cases = {case.name: case for case in profile._workloads()}
    for operation in ("dumps", "dumpb"):
        shared = cases[f"{operation}/shared-{label}"]
        independent = cases[f"{operation}/independent-{label}"]
        shared_input = shared.call.args[0]
        independent_input = independent.call.args[0]
        assert len(shared_input) == len(independent_input) == 256
        assert all(row is shared_input[0] for row in shared_input)
        assert len({id(row) for row in independent_input}) == 256
        expected = shared.reference()
        assert independent.reference() == expected
        assert shared.call() == independent.call() == expected
        assert shared.input_bytes == independent.input_bytes
        # A previous call must never hide later mutation of a shared row.
        original = shared_input[0][0]
        shared_input[0][0] = -0.0
        assert shared.call() == shared.reference()
        assert shared.call() != expected
        shared_input[0][0] = original


def test_real_installed_native_probes(tmp_path):
    pytest.importorskip("orjson")
    pytest.importorskip("msgspec")
    pytest.importorskip("unstd.serde._json_native")
    # Earlier harness tests profile or replace codecs. CPython 3.12/3.13 retain
    # monitoring state that correctly disables native guards after profiling.
    # Qualify the installed product in the same fresh-process context as timing.
    result = run_python(
        """
import json
from bench.profiles.jsonx_throughput import _native_checks
from unstd.serde import jsonx
print(json.dumps(_native_checks(jsonx)))
""",
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(json.loads(result.stdout)["probes"].values())


@dataclass
class _Measurement:
    per_op_ns: float = 10.0
    samples_ns: tuple[float, ...] = (10.0, 11.0)
    cpu_samples_ns: tuple[float, ...] = (1.0, 2.0)


def test_paired_batches_stay_bounded_when_one_codec_is_much_faster(monkeypatch):
    def slow():
        return "same"

    def fast():
        return "same"

    def control():
        return "same"

    def calibrate(function):
        if function is slow:
            return 1, 10_000_000
        if function is fast:
            return 100_000, 10_000_000
        return 10_000, 10_000_000

    batches = []

    def batch(function, count):
        per_op = 10_000_000 if function is slow else 100 if function is fast else 1000
        batches.append((function, count))
        assert count * per_op <= profile.timing._MAX_BATCH_NS
        return count * per_op

    monkeypatch.setattr(profile, "_calibrate", calibrate)
    monkeypatch.setattr(profile.timing, "_batch", batch)
    cases = {
        "before": profile.Workload("test", slow, control, 4),
        "after": profile.Workload("test", fast, control, 4),
    }
    measurements, pairs = profile._paired_measure(cases, 3, 0)
    assert measurements["before"]["candidate"]["iters"] == 25
    assert measurements["after"]["candidate"]["iters"] == 25
    assert len(pairs) == 3
    assert all(pair["after_over_before"] == 0.00001 for pair in pairs)
    assert len(batches) == 12


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
    if "--paired-worker" in command:
        before = command[command.index("--compare-source") + 1]
        repetition = int(command[command.index("--worker-repetition") + 1])
        rounds = int(command[command.index("--rounds") + 1])
        pairs = [
            {
                "round": index,
                "before_ns": 10.0,
                "after_ns": 20.0,
                "after_over_before": 2.0,
                "before_stdlib_ns": 100.0,
                "after_stdlib_ns": 100.0,
            }
            for index in range(rounds)
        ]
        return json.dumps(
            [
                {
                    "label": label,
                    "repetition": repetition,
                    "environment": {"same": True},
                    "provenance": {"source": root},
                    "timings": {
                        "records": {
                            "candidate": {
                                "per_op_ns": 10.0 if label == "before" else 20.0,
                                "cpu_samples_ns": [0.1],
                            },
                            "stdlib": {"per_op_ns": 100.0},
                            "candidate_mib_s": 1.0,
                            "paired_batches": pairs,
                        }
                    },
                }
                for label, root in (("before", before), ("after", source))
            ]
        )
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
    environments = []

    def worker(command, **kwargs):
        observed.append(command)
        environments.append(kwargs["env"])
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
    assert len(observed) == 2
    assert all("--paired-worker" in command for command in observed)
    assert all(
        command[command.index("--source") + 1] == str(tmp_path / "after")
        and command[command.index("--compare-source") + 1] == str(tmp_path / "before")
        for command in observed
    )
    assert all("--require-native" in command for command in observed)
    assert all(
        environment["PYTHONPATH"].split(profile.os.pathsep)
        == [str(tmp_path / "after"), str(Path(profile.__file__).resolve().parents[2])]
        for environment in environments
    )
    report = json.loads(output.read_text())
    assert report["regressions"] == ["records"]
    assert report["comparisons"]["records"]["before_over_after"] == 0.5
    assert len(report["runs"]) == 4


def test_paired_worker_bootstraps_without_a_globally_installed_product(
    tmp_path, monkeypatch
):
    # Reproduce bench's eager product import in a dependency-only interpreter.
    # -S excludes the developer environment's installed unstd, so omitting the
    # selected --target root fails here as it does after CI's --no-install-project.
    harness = tmp_path / "harness"
    profiles = harness / "bench/profiles"
    profiles.mkdir(parents=True)
    (profiles.parent / "__init__.py").write_text("import unstd\n")
    (profiles / "__init__.py").write_text("")
    source = tmp_path / "after"
    (source / "unstd").mkdir(parents=True)
    (source / "unstd/__init__.py").write_text("")
    arguments = [
        "--compare-source",
        str(tmp_path / "before"),
        "--source",
        str(source),
        "--rounds",
        "1",
        "--repetitions",
        "1",
        "--output",
        str(tmp_path / "report.json"),
    ]
    worker = profiles / "jsonx_throughput.py"
    result = _simulated_worker(
        ["--paired-worker", "--worker-repetition", "0", *arguments]
    )
    worker.write_text(
        "import sys\nfrom pathlib import Path\nimport unstd\n"
        "source = Path(sys.argv[sys.argv.index('--source') + 1]).resolve()\n"
        "assert Path(unstd.__file__).resolve().parent == source / 'unstd'\n"
        f"print({result!r})\n"
    )
    check_output = profile.subprocess.check_output

    def dependency_only_worker(command, **kwargs):
        return check_output(
            [command[0], "-S", *command[1:]], cwd=tmp_path, timeout=30, **kwargs
        )

    monkeypatch.setattr(profile, "__file__", str(worker))
    monkeypatch.setattr(profile.subprocess, "check_output", dependency_only_worker)
    profile.main(arguments)
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["runs"]) == 2
    assert report["runs"][1]["provenance"]["source"] == str(source)


@pytest.mark.parametrize(
    ("ratios", "fails"),
    [((1.10, 1.16, 1.10), False), ((1.20, 1.30, 1.20), True)],
)
def test_wall_gate_pairs_repetitions_under_drift_and_keeps_persistent_regressions(
    tmp_path, monkeypatch, ratios, fails
):
    observed = []
    before_samples = (100.0, 105.0, 200.0)

    def worker(command, **kwargs):
        repetition = int(command[command.index("--worker-repetition") + 1])
        observed.append(repetition)
        results = json.loads(_simulated_worker(command, **kwargs))
        for result in results:
            row = result["timings"]["records"]
            row["candidate"]["per_op_ns"] = before_samples[repetition] * (
                ratios[repetition] if result["label"] == "after" else 1.0
            )
            # Controls remain diagnostic and never replace wall samples.
            row["stdlib"]["per_op_ns"] = before_samples[repetition] * 10
            for pair in row["paired_batches"]:
                pair["before_ns"] = before_samples[repetition]
                pair["after_ns"] = before_samples[repetition] * ratios[repetition]
                pair["after_over_before"] = ratios[repetition]
        return json.dumps(results)

    monkeypatch.setattr(profile.subprocess, "check_output", worker)
    output = tmp_path / "paired.json"
    arguments = [
        "--compare-source",
        str(tmp_path / "before"),
        "--source",
        str(tmp_path / "after"),
        "--max-slowdown",
        "1.15",
        "--rounds",
        "7",
        "--repetitions",
        "3",
        "--output",
        str(output),
    ]
    if fails:
        with pytest.raises(RuntimeError, match="wall-clock slowdown exceeds"):
            profile.main(arguments)
    else:
        profile.main(arguments)
    assert observed == [0, 1, 2]
    report = json.loads(output.read_text())
    comparison = report["comparisons"]["records"]
    assert report["max_slowdown"] == 1.15
    assert comparison["paired_after_over_before"] == pytest.approx(ratios[0])
    assert [pair["after_over_before"] for pair in comparison["pairs"]] == (
        pytest.approx(ratios)
    )
    assert comparison["before_ns"] == 105
    # Independently taking medians selects the slow middle pair, which is
    # already retained in the complete pair evidence rather than hidden.
    assert comparison["after_ns"] / comparison["before_ns"] > 1.15
    assert report["regressions"] == (["records"] if fails else [])
    assert len(report["runs"]) == 6
    assert len(comparison["batch_pairs"]) == 21


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
