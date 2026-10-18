"""Public fuzz commands authenticate inputs, route engines, and publish evidence.

Real subprocess smoke covers corpus replay, reproducible bounded mutation and
single-input replay. Reviewed failing workers prove stable nonzero exits and
byte-exact artifacts. Routing probes ensure manifests never become fuzzer input
and failed preflight never starts a coverage campaign.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.tooling.fuzz import __main__ as cli
from tests.tooling.fuzz import execution, registry


REPO = Path(__file__).resolve().parents[5]


def _command(*arguments):
    return subprocess.run(  # noqa: S603 - fixed interpreter and repository module
        [sys.executable, "-m", "tests.tooling.fuzz", *map(str, arguments)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _read_report(directory, stage):
    return json.loads(
        (directory / "json" / "stdlib" / stage / "report.json").read_text()
    )


def _forbidden(*_args, **_kwargs):
    pytest.fail("execution occurred before CLI admission succeeded")


def test_public_matrix_lists_every_reviewed_target_once():
    result = _command("list", "--json")
    assert result.returncode == 0, result.stderr
    matrix = json.loads(result.stdout)
    assert matrix == {
        "include": [{"target": target.name} for target in registry.check()]
    }
    assert len(matrix["include"]) == 11


def test_full_ci_matrix_contains_every_supported_target_backend_pair_once():
    result = _command("list", "--json", "--all-backends")
    assert result.returncode == 0, result.stderr
    matrix = json.loads(result.stdout)
    expected = [
        {"target": target.name, "backend": backend}
        for target in registry.check()
        for backend in target.backends
    ]
    assert matrix == {"include": expected}
    assert len(expected) == 19
    assert sum(row["backend"] == "installed" for row in expected) == 11
    assert sum(row["backend"] == "stdlib" for row in expected) == 8


def test_list_and_check_authenticate_all_seeds_and_display_actual_counts(capsys):
    targets = registry.check()
    assert cli.main(["list"]) == 0
    listing = capsys.readouterr().out
    for target in targets:
        assert f"{target.name}: " in listing
        assert f"{len(registry.load_seeds(target))} seeds" in listing
    assert cli.main(["check"]) == 0
    count = sum(len(registry.load_seeds(target)) for target in targets)
    assert (
        capsys.readouterr().out == f"Validated 11 targets and {count} reviewed seeds\n"
    )


def test_real_public_replay_executes_every_seed_and_writes_complete_provenance(
    tmp_path,
):
    result = _command(
        "replay", "--target", "json", "--backend", "stdlib", "--artifact-dir", tmp_path
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    target = next(target for target in registry.check() if target.name == "json")
    seeds = registry.load_seeds(target)
    report = _read_report(tmp_path, "replay")
    assert report["status"] == "passed"
    assert report["attempted"] == report["completed"] == report["cases"] == len(seeds)
    assert report["reviewed_corpus_sha256"] == registry.corpus_digest(seeds)
    assert report["stage"] == "replay"
    assert report["backend_modules"] == {}
    assert report["limits"]["max_input_bytes"] == target.max_input_bytes
    assert report["worker_returncode"] == 0
    source = report["provenance"]["source"]
    assert set(source) == {"revision", "dirty"}
    assert source["revision"] is None or len(source["revision"]) == 40
    assert source["dirty"] in (True, False, None)
    for name, digest in report["provenance"]["tooling_sha256"].items():
        assert digest == hashlib.sha256((registry.ROOT / name).read_bytes()).hexdigest()
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["status"] == "passed"
    assert summary["unsupported_targets"] == []
    assert summary["reports"] == [
        {
            "target": "json",
            "stage": "replay",
            "status": "passed",
            "completed": len(seeds),
            "path": str(tmp_path / "json/stdlib/replay/report.json"),
        }
    ]


def test_real_mutation_command_is_counted_bounded_and_repeatable(tmp_path):
    digests = []
    for attempt in range(2):
        directory = tmp_path / str(attempt)
        result = _command(
            "campaign",
            "--target",
            "json",
            "--backend",
            "stdlib",
            "--engine",
            "deterministic",
            "--runs",
            "18",
            "--seed",
            "31415",
            "--artifact-dir",
            directory,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        report = _read_report(directory, "campaign")
        preflight = _read_report(directory, "replay")
        assert preflight["status"] == report["status"] == "passed"
        assert report["attempted"] == report["completed"] == report["runs"] == 18
        assert report["engine"] == "deterministic-mutation"
        assert report["seed"] == 31415
        assert report["stage"] == "campaign"
        digests.append(report["corpus_sha256"])
        summary = json.loads((directory / "summary.json").read_text())
        assert [item["stage"] for item in summary["reports"]] == ["replay", "campaign"]
    assert digests[0] == digests[1]


def test_real_single_input_command_replays_exact_binary_file(tmp_path):
    raw = b'\0{"synthetic":"\\ud800","list":[1,null,true]}'
    input_path = tmp_path / "reviewed.bin"
    input_path.write_bytes(raw)
    output = tmp_path / "evidence"
    result = _command(
        "reproduce",
        "--target",
        "json",
        "--backend",
        "stdlib",
        "--input",
        input_path,
        "--artifact-dir",
        output,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = _read_report(output, "reproduce")
    assert report["completed"] == report["attempted"] == report["cases"] == 1
    assert report["stage"] == "reproduce"
    assert input_path.read_bytes() == raw


def test_actual_finding_returns_one_and_retains_exact_reproducer_with_provenance(
    tmp_path, monkeypatch
):
    real_run_inputs = execution.run_inputs

    def failing_worker(target, cases, **limits):
        return real_run_inputs(target, cases, _fixture="assertion", **limits)

    monkeypatch.setattr(execution, "run_inputs", failing_worker)
    raw = b"\0exact hostile bytes\xff\xfe\0"
    source = tmp_path / "original.bin"
    source.write_bytes(raw)
    output = tmp_path / "evidence"
    assert (
        cli.main(
            [
                "reproduce",
                "--target",
                "json",
                "--backend",
                "stdlib",
                "--input",
                str(source),
                "--artifact-dir",
                str(output),
            ]
        )
        == 1
    )
    report = _read_report(output, "reproduce")
    assert report["failure"]["kind"] == "assertion"
    assert report["failure"]["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert Path(report["failure"]["input_path"]).read_bytes() == raw
    assert (
        report["failure"]["case_id"] == "reproduce/" + hashlib.sha256(raw).hexdigest()
    )
    assert report["provenance"]["input_version"] == 1
    assert report["reviewed_corpus_sha256"]
    assert json.loads((output / "summary.json").read_text())["status"] == "failed"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--wall-seconds", "0"],
        ["--wall-seconds", "nan"],
        ["--per-input-seconds", "inf"],
        ["--cpu-seconds", "0"],
        ["--memory-mb", "0"],
        ["--max-input-bytes", "65537"],
        ["--runs", "0"],
        ["--runs", "100001"],
        ["--seed", "0"],
        ["--seed", "4294967296"],
    ],
)
def test_invalid_budgets_have_stable_admission_exit_without_execution(
    tmp_path, monkeypatch, arguments, capsys
):
    monkeypatch.setattr(execution, "run_inputs", _forbidden)
    assert (
        cli.main(
            [
                "campaign",
                "--target",
                "json",
                "--backend",
                "stdlib",
                "--artifact-dir",
                str(tmp_path),
                *arguments,
            ]
        )
        == 2
    )
    assert "Fuzz admission failed:" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "target", ["missing", "../../outside", "tokens", "yaml", "structs"]
)
def test_unknown_or_unsupported_target_is_never_silently_skipped(
    tmp_path, monkeypatch, target, capsys
):
    monkeypatch.setattr(execution, "run_inputs", _forbidden)
    assert (
        cli.main(
            [
                "replay",
                "--target",
                target,
                "--backend",
                "stdlib",
                "--artifact-dir",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "Fuzz admission failed:" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_all_stdlib_selects_exactly_supported_targets_and_reports_exclusions(
    tmp_path, monkeypatch
):
    seen = []

    def recorded(target, cases, **limits):
        assert limits["backend"] == "stdlib"
        assert cases
        seen.append(target)
        return {"status": "passed", "completed": len(cases)}

    monkeypatch.setattr(execution, "run_inputs", recorded)
    assert (
        cli.main(["replay", "--backend", "stdlib", "--artifact-dir", str(tmp_path)])
        == 0
    )
    assert set(seen) == {
        "base64",
        "gzip",
        "json",
        "ndjson",
        "pack",
        "rex",
        "toml",
        "wav",
    }
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["unsupported_targets"] == ["structs", "tokens", "yaml"]
    assert len(summary["reports"]) == 8


def test_atheris_routing_excludes_manifests_and_forwards_exact_authenticated_seeds(
    tmp_path, monkeypatch
):
    target = next(target for target in registry.check() if target.name == "json")
    seeds = registry.load_seeds(target)
    calls = []

    def preflight(name, cases, **limits):
        assert cases == [(seed.identity, seed.data) for seed in seeds]
        calls.append("replay")
        return {"status": "passed", "completed": len(cases)}

    def engine(name, directory, **options):
        assert calls == ["replay"]
        assert name == "json"
        assert {path.name: path.read_bytes() for path in directory.iterdir()} == {
            seed.file: seed.data for seed in seeds
        }
        assert not (directory / "seeds.json").exists()
        assert options["runs"] == 27
        assert options["seed"] == 42
        assert options["backend"] == "stdlib"
        calls.append("atheris")
        return {"status": "passed", "completed": 27, "engine": "atheris-libfuzzer"}

    monkeypatch.setattr(execution, "run_inputs", preflight)
    monkeypatch.setattr(execution, "run_atheris", engine)
    assert (
        cli.main(
            [
                "campaign",
                "--target",
                "json",
                "--backend",
                "stdlib",
                "--engine",
                "atheris",
                "--runs",
                "27",
                "--seed",
                "42",
                "--artifact-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert calls == ["replay", "atheris"]


def test_failed_replay_prevents_campaign_and_preserves_failure_exit(
    tmp_path, monkeypatch
):
    def preflight(*_args, **_limits):
        return {
            "status": "failed",
            "completed": 0,
            "failure": {"kind": "startup_error"},
        }

    monkeypatch.setattr(execution, "run_inputs", preflight)
    monkeypatch.setattr(execution, "run_atheris", _forbidden)
    assert (
        cli.main(
            [
                "campaign",
                "--target",
                "json",
                "--backend",
                "stdlib",
                "--engine",
                "atheris",
                "--artifact-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert [report["stage"] for report in summary["reports"]] == ["replay"]


def test_corrupted_inventory_prevents_any_execution(tmp_path, monkeypatch, capsys):
    def corrupted():
        msg = "reviewed corpus digest mismatch"
        raise registry.RegistryError(msg)

    monkeypatch.setattr(registry, "check", corrupted)
    monkeypatch.setattr(execution, "run_inputs", _forbidden)
    assert cli.main(["replay", "--artifact-dir", str(tmp_path)]) == 2
    assert "digest mismatch" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_reproduce_requires_one_target_and_a_readable_bounded_file(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(execution, "run_inputs", _forbidden)
    missing = tmp_path / "absent.bin"
    assert (
        cli.main(
            [
                "reproduce",
                "--target",
                "all",
                "--input",
                str(missing),
                "--artifact-dir",
                str(tmp_path / "all"),
            ]
        )
        == 2
    )
    assert (
        cli.main(
            [
                "reproduce",
                "--target",
                "json",
                "--backend",
                "stdlib",
                "--input",
                str(missing),
                "--artifact-dir",
                str(tmp_path / "missing"),
            ]
        )
        == 2
    )


def test_real_oversized_reproducer_has_admission_exit_and_no_green_report(tmp_path):
    source = tmp_path / "too-large.bin"
    source.write_bytes(b"x" * 65537)
    output = tmp_path / "evidence"
    result = _command(
        "reproduce",
        "--target",
        "json",
        "--backend",
        "stdlib",
        "--input",
        source,
        "--artifact-dir",
        output,
    )
    assert result.returncode == 2
    assert "exceeds max_input_bytes" in result.stderr
    assert not (output / "summary.json").exists()
