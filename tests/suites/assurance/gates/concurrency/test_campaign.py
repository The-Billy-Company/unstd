"""Concurrency lane admission, real bounded controls and semantic replay.

The shared chaos engine retains its original resource/accounting controls. These
gates exercise the new lane boundary: selected files, journal namespace, source
provenance, cross-lane refusal and real CLI propagation. A replay repeats the
reviewed schedule and result laws; actual thread completion order may differ.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal

import pytest

from tests.support.anchors import REPO_ROOT
from tests.support.process import run_python
from tests.tooling.chaos import __main__ as shared_cli
from tests.tooling.chaos import execution
from tests.tooling.concurrency import __main__ as cli
from tests.tooling.runner import check_inventory


def _command(*arguments):
    return run_python(
        "from tests.tooling.concurrency.__main__ import main; raise SystemExit(main())",
        tuple(map(str, arguments)),
        cwd=REPO_ROOT,
        timeout=55,
    )


def _read(directory):
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["lane"] == "concurrency"
    assert len(summary["reports"]) == 1
    path = Path(summary["reports"][0]["path"])
    assert path.is_relative_to(directory)
    return summary, json.loads(path.read_text()), path


def test_real_matrix_and_listing_derive_only_the_active_concurrency_lane(capsys):
    lane = next(
        suite for suite in check_inventory(REPO_ROOT) if suite.id == "concurrency"
    )
    assert lane.status == "active"
    assert lane.marker == "concurrency"
    groups = execution.discover("concurrency")
    assert set(groups) == {"filesystem", "ids", "shared_state"}
    assert not groups.keys() & execution.discover("chaos").keys()
    assert cli.main(["list", "--json"]) == 0
    matrix = json.loads(capsys.readouterr().out)
    expected = [
        {"group": group, "backend": backend, "os": system}
        for group in groups
        for backend in ("installed", "stdlib")
        for system in ("ubuntu-latest", "macos-latest")
    ]
    assert matrix == {"include": expected}
    assert len({tuple(sorted(row.items())) for row in matrix["include"]}) == 12
    assert all(path.parent == REPO_ROOT / lane.paths[0] for path in groups.values())
    assert cli.main(["check"]) == 0
    assert capsys.readouterr().out == "Validated 3 executable concurrency groups\n"


def test_default_pytest_excludes_scenarios_but_explicit_suite_runner_collects_them():
    selected = execution.discover("concurrency")["ids"].relative_to(REPO_ROOT)
    default = run_python(
        "import pytest,sys; raise SystemExit(pytest.main(sys.argv[1:]))",
        (str(selected), "--collect-only", "-q"),
        cwd=REPO_ROOT,
        timeout=20,
    )
    assert default.returncode == 5, (default.stdout, default.stderr)
    assert "::test_" not in default.stdout
    explicit = run_python(
        "from tests.tooling.runner import main; raise SystemExit(main())",
        ("run", "concurrency", "--", "--collect-only", "-q"),
        cwd=REPO_ROOT,
        timeout=20,
    )
    assert explicit.returncode == 0, (explicit.stdout, explicit.stderr)
    assert "test_ids.py::test_fallback_thread_contention" in explicit.stdout
    assert "test_filesystem.py::test_" in explicit.stdout
    assert "test_shared_state.py::test_" in explicit.stdout
    assert "/adversarial/chaos/" not in explicit.stdout


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [
        ("pass", None),
        ("assertion", "assertion"),
        ("hang", "test_timeout"),
        ("abort", "native_crash"),
    ],
)
def test_reviewed_controls_preserve_lane_identity_journals_limits_and_exit_status(
    tmp_path, monkeypatch, fixture, kind
):
    if fixture == "abort" and os.name != "posix":
        pytest.skip("POSIX abort signal classification")
    real_run = execution.run_group
    selected = []

    def reviewed(group, **options):
        selected.append((group, options["lane"]))
        return real_run(group, _fixture=fixture, **options)

    monkeypatch.setattr(execution, "run_group", reviewed)
    status = cli.main(
        [
            "run",
            "--group",
            "ids",
            "--backend",
            "stdlib",
            "--seed",
            "271",
            "--wall-seconds",
            "8",
            "--per-test-seconds",
            "0.3",
            "--cpu-seconds",
            "5",
            "--artifact-dir",
            str(tmp_path),
        ]
    )
    assert selected == [("ids", "concurrency")]
    assert status == (0 if kind is None else 1)
    summary, report, _ = _read(tmp_path)
    assert (
        summary["status"]
        == report["status"]
        == ("passed" if kind is None else "failed")
    )
    assert report["lane"] == report["admission"]["lane"] == "concurrency"
    assert report["group"] == "ids"
    assert report["seed"] == 271
    assert report["backend"] == "stdlib"
    assert report["backend_modules"] == {}
    assert report["limits"]["per_test_seconds"] == 0.3
    assert report["limits"]["wall_seconds"] == 8
    for source in (
        "tests/suites/assurance/adversarial/concurrency/test_ids.py",
        "tests/support/concurrency_ids.py",
        "tests/tooling/concurrency/__main__.py",
        "tests/tooling/chaos/worker.py",
        "src/unstd/ids/uid.py",
    ):
        assert (
            report["source_sha256"][source]
            == hashlib.sha256((REPO_ROOT / source).read_bytes()).hexdigest()
        )
    assert not any(
        "/adversarial/chaos/" in source for source in report["source_sha256"]
    )
    assert report["journals"]
    for nodeid, journal in report["journals"].items():
        assert journal["seed"] == 271
        assert journal["scenario"] == nodeid
        assert journal["events"][0] == {
            "event": "reviewed.fixture",
            "fixture": fixture,
            "seed": 271,
        }
    properties = [case["properties"] for case in report["cases"]]
    assert not any(
        "chaos_journal" in values or "chaos_seed" in values for values in properties
    )
    if kind is None:
        assert report["passed"] == report["collected"] == 1
        assert report["failed"] == 0
        assert any("concurrency_journal" in values for values in properties)
    else:
        assert report["failure"]["kind"] == kind
        if fixture in {"hang", "abort"}:
            assert report["live_journals"]
            assert all(
                journal["events"][-1]["fault"] == fixture
                for journal in report["live_journals"].values()
            )
            assert not (
                Path(summary["reports"][0]["path"]).parent / "complete.json"
            ).exists()
        if fixture == "abort":
            assert report["worker_returncode"] == -signal.SIGABRT


def test_real_run_and_replay_preserve_schedule_and_validate_each_new_history(tmp_path):
    original, replayed = tmp_path / "original", tmp_path / "replayed"
    first = _command(
        "run",
        "--group",
        "ids",
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
        "--artifact-dir",
        original,
    )
    assert first.returncode == 0, (first.stdout, first.stderr)
    _, prior, report_path = _read(original)
    saved = report_path.read_bytes()
    second = _command("replay", report_path, "--artifact-dir", replayed)
    assert second.returncode == 0, (second.stdout, second.stderr)
    summary, current, _ = _read(replayed)
    assert summary["command"] == "replay"
    assert current["status"] == prior["status"] == "passed"
    for field in (
        "lane",
        "group",
        "backend",
        "seed",
        "limits",
        "schedule_version",
        "passed",
        "skipped",
    ):
        assert current[field] == prior[field]
    assert prior["collected"] == 29
    assert prior["passed"] == (28 if hasattr(os, "fork") else 26)
    assert prior["skipped"] == (1 if hasattr(os, "fork") else 3)

    def plans(report):
        return {
            nodeid: [
                event for event in journal["events"] if event["event"] == "ids.plan"
            ]
            for nodeid, journal in report["journals"].items()
        }

    assert plans(current) == plans(prior)
    assert any(plans(current).values())
    assert set(current["collection"]["nodeids"]) == set(prior["collection"]["nodeids"])
    for report in (prior, current):
        passed = {
            case["nodeid"]
            for case in report["cases"]
            if case["phase"] == "call" and case["outcome"] == "passed"
        }
        assert len(passed) == report["passed"]
        assert passed <= report["journals"].keys()
        assert all(
            report["journals"][nodeid]["seed"] == 4294967297 for nodeid in passed
        )
        assert not any("/adversarial/chaos/" in nodeid for nodeid in passed)
    assert report_path.read_bytes() == saved


def test_inherited_pytest_selectors_and_explicit_plugins_cannot_shrink_or_replace_campaign(
    tmp_path, monkeypatch
):
    # The selector alone previously produced a green one-case "ids" campaign;
    # PYTEST_PLUGINS also bypasses PYTEST_DISABLE_PLUGIN_AUTOLOAD. Neither may
    # influence the child-owned reviewed collection or import a hidden plugin.
    monkeypatch.setenv(
        "PYTEST_ADDOPTS",
        "-k test_overlapping_reservations_may_complete_in_reverse_order",
    )
    monkeypatch.setenv("PYTEST_PLUGINS", "unstd_unreviewed_plugin_must_not_load")
    completed = _command(
        "run",
        "--group",
        "ids",
        "--backend",
        "stdlib",
        "--wall-seconds",
        "30",
        "--per-test-seconds",
        "15",
        "--cpu-seconds",
        "20",
        "--artifact-dir",
        tmp_path,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    _, report, _ = _read(tmp_path)
    skipped = 1 if hasattr(os, "fork") else 3
    assert report["status"] == "passed"
    assert report["collected"] == 29
    assert report["passed"] == 29 - skipped
    assert report["skipped"] == skipped
    assert report["failed"] == 0
    assert len(report["collection"]["nodeids"]) == 29
    assert all(
        "/adversarial/concurrency/test_ids.py::" in nodeid
        for nodeid in report["collection"]["nodeids"]
    )


def _replay_data(lane):
    data = {
        "schema_version": 1,
        "schedule_version": 1,
        "group": "ids" if lane == "concurrency" else "clock_entropy",
        "backend": "stdlib",
        "seed": 173,
        "limits": {
            "wall_seconds": 30,
            "per_test_seconds": 15,
            "cpu_seconds": 20,
            "memory_mb": 2048,
        },
    }
    if lane is not None:
        data["lane"] = lane
    return data


@pytest.mark.parametrize("lane", [None, "chaos", "other"])
def test_concurrency_replay_refuses_legacy_chaos_and_foreign_lane_before_execution(
    tmp_path, monkeypatch, lane, capsys
):
    def forbidden(*_args, **_options):
        pytest.fail("foreign-lane replay launched a worker")

    monkeypatch.setattr(execution, "run_group", forbidden)
    source = tmp_path / "foreign.json"
    source.write_text(json.dumps(_replay_data(lane)))
    destination = tmp_path / "must-not-exist"
    assert cli.main(["replay", str(source), "--artifact-dir", str(destination)]) == 2
    assert "different campaign lane" in capsys.readouterr().err
    assert not destination.exists()


def test_legacy_chaos_replay_stays_compatible_and_concurrency_cannot_cross_back(
    tmp_path, monkeypatch
):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(_replay_data(None)))
    assert shared_cli._read_replay(legacy, lane="chaos")["group"] == "clock_entropy"
    modern = tmp_path / "modern.json"
    modern.write_text(json.dumps(_replay_data("concurrency")))

    def forbidden(*_args, **_options):
        pytest.fail("concurrency replay crossed into chaos execution")

    monkeypatch.setattr(execution, "run_group", forbidden)
    destination = tmp_path / "must-not-exist"
    assert (
        shared_cli.main(["replay", str(modern), "--artifact-dir", str(destination)])
        == 2
    )
    assert not destination.exists()


def test_all_selects_each_concurrency_group_once_with_a_scoped_worker(
    tmp_path, monkeypatch
):
    real_run = execution.run_group
    selected = []

    def reviewed(group, **options):
        selected.append((group, options["lane"], options["seed"]))
        return real_run(group, _fixture="pass", **options)

    monkeypatch.setattr(execution, "run_group", reviewed)
    assert (
        cli.main(
            [
                "run",
                "--group",
                "all",
                "--backend",
                "stdlib",
                "--seed",
                "173",
                "--artifact-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert selected == [
        (group, "concurrency", 173) for group in execution.discover("concurrency")
    ]
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["lane"] == "concurrency"
    assert summary["status"] == "passed"
    assert len(summary["reports"]) == 3
    assert len({item["path"] for item in summary["reports"]}) == 3
    assert all(Path(item["path"]).is_file() for item in summary["reports"])
