"""Public chaos commands admit reviewed groups and retain replayable evidence.

Real CLI workers prove stream and clock/entropy execution and deterministic
replay. Reviewed control workers exercise orchestration failures without
replacing result accounting. Invalid selectors or budgets cannot launch a test
worker, create an output directory, or silently select a different group.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.tooling.chaos import __main__ as cli
from tests.tooling.chaos import execution


REPO = Path(__file__).resolve().parents[5]


def _command(*arguments):
    return subprocess.run(  # noqa: S603 — fixed interpreter and repository module
        [sys.executable, "-m", "tests.tooling.chaos", *map(str, arguments)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _forbidden(*_args, **_kwargs):
    pytest.fail("worker execution occurred before CLI admission succeeded")


def _summary(directory):
    return json.loads((directory / "summary.json").read_text())


def _report(directory):
    summary = _summary(directory)
    assert len(summary["reports"]) == 1
    report_path = Path(summary["reports"][0]["path"])
    assert report_path.is_relative_to(directory)
    return json.loads(report_path.read_text()), report_path


def _journal_digest(journals):
    # Evidence identity contains fault observations, not process placement or
    # scheduling measurements. PIDs and elapsed values can change on replay.
    volatile = {
        "pid",
        "child_pid",
        "duration_seconds",
        "started_monotonic",
        "elapsed_seconds",
    }

    def normalize(value):
        if isinstance(value, dict):
            return {
                key: normalize(item)
                for key, item in value.items()
                if key not in volatile
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    return hashlib.sha256(
        json.dumps(normalize(journals), sort_keys=True).encode()
    ).hexdigest()


def test_real_ci_matrix_covers_every_registered_group_backend_and_platform_once():
    result = _command("list", "--json")
    assert result.returncode == 0, result.stderr
    expected = [
        {"group": group, "backend": backend, "os": system}
        for group in execution.discover()
        for backend in ("installed", "stdlib")
        for system in ("ubuntu-latest", "macos-latest")
    ]
    assert json.loads(result.stdout) == {"include": expected}
    assert len(expected) == 12
    assert len({tuple(sorted(row.items())) for row in expected}) == len(expected)


def test_list_and_check_show_actual_executable_groups(capsys):
    groups = execution.discover()
    assert cli.main(["list"]) == 0
    listing = capsys.readouterr().out
    for group, path in groups.items():
        assert f"{group}: {path.relative_to(REPO)}" in listing
    assert cli.main(["check"]) == 0
    assert (
        capsys.readouterr().out == f"Validated {len(groups)} executable chaos groups\n"
    )


@pytest.mark.parametrize("group", ["streams", "clock_entropy"])
def test_real_cli_workers_publish_complete_observations_and_hard_limits(
    tmp_path, group
):
    result = _command(
        "run",
        "--group",
        group,
        "--backend",
        "stdlib",
        "--seed",
        "4294967297",
        "--wall-seconds",
        "30",
        "--per-test-seconds",
        "15",
        "--cpu-seconds",
        "20",
        "--memory-mb",
        "2048",
        "--artifact-dir",
        tmp_path,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    summary = _summary(tmp_path)
    assert summary["status"] == "passed"
    report, _ = _report(tmp_path)
    assert report["status"] == "passed"
    assert report["worker_returncode"] == 0
    assert report["group"] == group
    assert report["backend"] == "stdlib"
    assert report["seed"] == 4294967297
    assert report["passed"] > 0
    assert report["failed"] == 0
    assert report["limits"] == {
        "wall_seconds": 30,
        "per_test_seconds": 15,
        "cpu_seconds": 20,
        "memory_mb": 2048,
    }
    passed = {
        case["nodeid"]
        for case in report["cases"]
        if case["phase"] == "call" and case["outcome"] == "passed"
    }
    assert len(passed) == report["passed"]
    assert passed <= set(report["journals"])
    for nodeid in passed:
        journal = report["journals"][nodeid]
        assert journal["seed"] == report["seed"]
        assert journal["scenario"] == nodeid
        assert journal["version"] == report["schedule_version"] == 1
        assert journal["events"]
    for path, digest in report["source_sha256"].items():
        assert digest == hashlib.sha256((REPO / path).read_bytes()).hexdigest(), path


def test_real_replay_reuses_exact_group_seed_backend_limits_and_fault_journal(tmp_path):
    original = tmp_path / "original"
    replayed = tmp_path / "replayed"
    first = _command(
        "run",
        "--group",
        "streams",
        "--backend",
        "stdlib",
        "--seed",
        "173",
        "--wall-seconds",
        "30",
        "--per-test-seconds",
        "15",
        "--cpu-seconds",
        "20",
        "--artifact-dir",
        original,
    )
    assert first.returncode == 0, (first.stdout, first.stderr)
    prior, path = _report(original)
    replay = _command("replay", path, "--artifact-dir", replayed)
    assert replay.returncode == 0, (replay.stdout, replay.stderr)
    current, _ = _report(replayed)
    assert _summary(replayed)["command"] == "replay"
    assert current["status"] == prior["status"] == "passed"
    for name in (
        "group",
        "seed",
        "backend",
        "limits",
        "schedule_version",
        "passed",
        "skipped",
    ):
        assert current[name] == prior[name]
    assert _journal_digest(current["journals"]) == _journal_digest(prior["journals"])
    assert path.read_text() == json.dumps(prior, indent=2, sort_keys=True) + "\n"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--seed", "-1"],
        ["--seed", str(2**64)],
        ["--rounds", "0"],
        ["--rounds", "101"],
        ["--wall-seconds", "0"],
        ["--wall-seconds", "nan"],
        ["--wall-seconds", "3601"],
        ["--per-test-seconds", "inf"],
        ["--per-test-seconds", "-1"],
        ["--cpu-seconds", "0"],
        ["--cpu-seconds", "3601"],
        ["--memory-mb", "0"],
        ["--memory-mb", "65537"],
        ["--group", "missing"],
        ["--group", "../../outside"],
        ["--group", "test_streams.py::test_example"],
        ["--group", "/unreviewed/test_outside.py"],
    ],
)
def test_bad_admission_never_launches_or_creates_artifacts(
    tmp_path, monkeypatch, arguments, capsys
):
    monkeypatch.setattr(execution, "run_group", _forbidden)
    output = tmp_path / "must-not-exist"
    assert cli.main(["run", "--artifact-dir", str(output), *arguments]) == 2
    assert "Chaos admission failed:" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    "arguments", [["--backend", "missing"], ["--rounds", "1.5"], ["--seed", "1.0"]]
)
def test_bad_argument_syntax_exits_two_without_artifacts(tmp_path, arguments):
    output = tmp_path / "must-not-exist"
    result = _command("run", "--artifact-dir", output, *arguments)
    assert result.returncode == 2
    assert not output.exists()


def test_actual_assertion_returns_one_and_preserves_failed_case_journal(
    tmp_path, monkeypatch, capsys
):
    real_run = execution.run_group

    def failing(group, **options):
        return real_run(group, _fixture="assertion", **options)

    monkeypatch.setattr(execution, "run_group", failing)
    # Ordinary CI gates deliberately provoke failures; only real campaign jobs
    # opt into GitHub reporting so these probes cannot pollute their summaries.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    github_summary = tmp_path / "github-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(github_summary))
    monkeypatch.delenv("UNSTD_CAMPAIGN_REPORTING", raising=False)
    assert (
        cli.main(
            [
                "run",
                "--group",
                "streams",
                "--backend",
                "stdlib",
                "--artifact-dir",
                str(tmp_path),
            ]
        )
        == 1
    )
    report, _ = _report(tmp_path)
    assert _summary(tmp_path)["status"] == report["status"] == "failed"
    assert report["failure"]["kind"] == "assertion"
    assert report["failed"] > 0
    assert report["journals"]
    assert any(case["outcome"] == "failed" for case in report["cases"])
    assert "::group::" not in capsys.readouterr().out
    assert not github_summary.exists()


def test_all_groups_and_seed_wrap_are_counted_without_overwriting_rounds(
    tmp_path, monkeypatch, capsys
):
    real_run = execution.run_group
    calls = []

    def control(group, **options):
        calls.append(
            (group, options["seed"], options["backend"], options["artifact_dir"])
        )
        return real_run(group, _fixture="pass", **options)

    monkeypatch.setattr(execution, "run_group", control)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("UNSTD_CAMPAIGN_REPORTING", "1")
    github_summary = tmp_path / "github-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(github_summary))
    assert (
        cli.main(
            [
                "run",
                "--group",
                "all",
                "--backend",
                "stdlib",
                "--seed",
                str(2**64 - 1),
                "--rounds",
                "2",
                "--artifact-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    expected = [
        (group, seed, "stdlib")
        for seed in (2**64 - 1, 0)
        for group in execution.discover()
    ]
    assert [call[:3] for call in calls] == expected
    assert len({call[3] for call in calls}) == len(expected)
    summary = _summary(tmp_path)
    assert len(summary["reports"]) == len(expected)
    assert summary["status"] == "passed"
    assert all(Path(item["path"]).is_file() for item in summary["reports"])
    logs = capsys.readouterr().out
    assert logs.count("::group::") == logs.count("::endgroup::") == len(expected)
    markdown = github_summary.read_text()
    assert "Fault injection / stdlib: passed" in markdown
    assert "Time | Replay report" in markdown
    for group, seed, _backend in expected:
        assert f"{group} | {seed} | passed" in markdown
    for item in summary["reports"]:
        assert str(Path(item["path"]).relative_to(tmp_path)) in markdown


def _valid_replay():
    return {
        "schema_version": 1,
        "schedule_version": 1,
        "group": "streams",
        "backend": "stdlib",
        "seed": 173,
        "limits": {
            "wall_seconds": 30,
            "per_test_seconds": 15,
            "cpu_seconds": 20,
            "memory_mb": 2048,
        },
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("schedule_version", 2),
        ("schedule_version", True),
        ("group", "../../outside"),
        ("group", []),
        ("backend", "other"),
        ("seed", -1),
        ("seed", True),
        ("seed", 2**64),
        ("limits", {}),
        ("limits", {"wall_seconds": 1}),
    ],
)
def test_untrusted_replay_metadata_cannot_launch_workers(
    tmp_path, monkeypatch, field, value
):
    monkeypatch.setattr(execution, "run_group", _forbidden)
    source = tmp_path / "untrusted.json"
    data = _valid_replay()
    data[field] = value
    source.write_text(json.dumps(data))
    output = tmp_path / "must-not-exist"
    assert cli.main(["replay", str(source), "--artifact-dir", str(output)]) == 2
    assert not output.exists()


@pytest.mark.parametrize(
    "field", ["wall_seconds", "per_test_seconds", "cpu_seconds", "memory_mb"]
)
def test_replay_numeric_types_and_bounds_are_admitted_before_execution(
    tmp_path, monkeypatch, field
):
    monkeypatch.setattr(execution, "run_group", _forbidden)
    source = tmp_path / "untrusted.json"
    data = _valid_replay()
    data["limits"][field] = True
    source.write_text(json.dumps(data))
    output = tmp_path / "must-not-exist"
    assert cli.main(["replay", str(source), "--artifact-dir", str(output)]) == 2
    assert not output.exists()


@pytest.mark.parametrize("body", ["", "{invalid", "[]", "null", " "])
def test_malformed_replay_has_stable_admission_exit(tmp_path, monkeypatch, body):
    monkeypatch.setattr(execution, "run_group", _forbidden)
    source = tmp_path / "malformed.json"
    source.write_text(body)
    assert (
        cli.main(["replay", str(source), "--artifact-dir", str(tmp_path / "output")])
        == 2
    )


def test_oversized_replay_is_bounded_before_json_parsing_or_execution(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(execution, "run_group", _forbidden)
    source = tmp_path / "oversized.json"
    source.write_bytes(b" " * (execution.MAX_REPORT + 1))
    assert (
        cli.main(["replay", str(source), "--artifact-dir", str(tmp_path / "output")])
        == 2
    )


def test_inventory_failure_prevents_listing_and_any_campaign(tmp_path, monkeypatch):
    def corrupted(_repo):
        message = "injected unowned test module"
        raise ValueError(message)

    monkeypatch.setattr(cli, "check_inventory", corrupted)
    monkeypatch.setattr(execution, "run_group", _forbidden)
    assert cli.main(["list", "--json"]) == 2
    assert cli.main(["run", "--artifact-dir", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []
