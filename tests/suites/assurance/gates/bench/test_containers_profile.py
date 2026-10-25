"""Container evidence checks isolate sources and keep oracles outside timing."""

from __future__ import annotations

from argparse import Namespace
import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.profiles import containers as profile


def _cases():
    pytest.importorskip("sortedcontainers")
    pytest.importorskip("immutables")
    import unstd.containers as containers

    return profile._cases(containers, size=32, batch=8)


def test_all_public_operation_oracles_run_without_clocks_in_smoke(monkeypatch):
    def forbidden():
        pytest.fail("smoke must not read timing clocks")

    monkeypatch.setattr(profile.time, "perf_counter_ns", forbidden)
    monkeypatch.setattr(profile.time, "process_time_ns", forbidden)
    cases = _cases()
    assert len(cases) == 16
    assert {case.name for case in cases} >= {
        "sorted_list.add",
        "sorted_list.keyed.remove",
        "sorted_list.bisect",
        "sorted_dict.insert",
        "sorted_set.add",
        "map.set",
        "map.update",
        "map.mutate",
    }
    for case in cases:
        assert profile._sample(case, 2, smoke=True) == {
            "iterations": 2,
            "operations_per_iteration": case.operations,
        }


@pytest.mark.parametrize("name", ["sorted_list.add", "sorted_dict.insert", "map.set"])
def test_corrupt_operation_outputs_fail_independent_models(name):
    case = next(case for case in _cases() if case.name == name)
    corrupted = replace(case, run=lambda _state: [])
    with pytest.raises(AssertionError, match="oracle mismatch"):
        profile._sample(corrupted, 1, smoke=True)


def test_state_setup_and_checks_remain_outside_timed_region(monkeypatch):
    events = []

    def setup():
        events.append("setup")
        return []

    def run(state):
        events.append("run")
        state.append(1)
        return state

    def check(state, result):
        events.append("check")
        assert state is result
        assert result == [1]

    wall = iter((100, 300))
    cpu = iter((200, 250))

    def wall_clock():
        events.append("wall")
        return next(wall)

    def cpu_clock():
        events.append("cpu")
        return next(cpu)

    monkeypatch.setattr(profile.time, "perf_counter_ns", wall_clock)
    monkeypatch.setattr(profile.time, "process_time_ns", cpu_clock)
    result = profile._sample(
        profile.Case("fixture", setup, run, check, 4), 2, smoke=False
    )
    assert events == [
        "setup",
        "setup",
        "cpu",
        "wall",
        "run",
        "run",
        "wall",
        "cpu",
        "check",
        "check",
    ]
    assert result["wall_ns_per_operation"] == 25
    assert result["cpu_ns_per_operation"] == 6.25


def test_wrong_product_provenance_is_rejected(tmp_path):
    import unstd.containers  # noqa: F401 — establish the real imported product.

    with pytest.raises(RuntimeError, match=r"requested .* imported"):
        profile._provenance(tmp_path)


@pytest.mark.parametrize("field", ["environment", "provenance", "cases"])
def test_drift_cannot_support_a_comparison(field):
    row = {
        "label": "current",
        "environment": {"dependency": "hash"},
        "provenance": {"source": "hash"},
        "cases": {"case": {}},
    }
    changed = copy.deepcopy(row)
    changed[field] = {"changed": "hash"}
    with pytest.raises(RuntimeError, match=r"differ|source changed"):
        profile._validate_runs([row, changed])


def test_source_order_alternates_and_smoke_has_no_cold_imports(tmp_path, monkeypatch):
    observed = []

    def run(command, source, cache):
        assert command[0] == profile.sys.executable
        assert command[1] == str(Path(profile.__file__).resolve())
        assert "--worker" in command
        assert "--smoke" in command
        assert cache.is_dir()
        observed.append(source.name)
        return {
            "environment": {"same": True},
            "provenance": {"source": str(source)},
            "cases": {"fixture": {}},
        }

    monkeypatch.setattr(profile, "_run", run)
    output = tmp_path / "report.json"
    args = Namespace(
        source=tmp_path / "current",
        compare_source=tmp_path / "before",
        rounds=3,
        size=32,
        batch=8,
        iterations=1,
        smoke=True,
        output=output,
    )
    report = profile._compare(args)
    assert observed == ["before", "current", "current", "before", "before", "current"]
    assert report["complete"]
    assert report["cold_imports"] == []
    assert report["warm_imports"] == []
    assert report["comparisons"] == {}
    assert output.is_file()


def test_cold_import_script_does_not_prewarm_harness_or_metadata():
    before_clock = profile._COLD_SCRIPT.split("cpu = time.process_time_ns()", 1)[0]
    assert "import sys, time" in before_clock
    assert "import json" not in before_clock
    assert "typing" not in before_clock
    assert "containers" not in before_clock


def test_both_sources_prewarm_one_shared_cache_before_measured_fresh_processes(
    tmp_path, monkeypatch
):
    observed = []

    def run(command, source, cache, *, warm=False):
        observed.append((source.name, cache, warm))
        if "-c" in command:
            return {
                "imported": str(source / "unstd/containers/__init__.py"),
                "backends_loaded": {"sortedcontainers": True, "immutables": True},
                "wall_ns": 200,
                "process_cpu_ns": 180,
            }
        return {
            "environment": {"same": True},
            "provenance": {"source": str(source)},
            "cases": {
                "fixture": {"wall_ns_per_operation": 2, "cpu_ns_per_operation": 1}
            },
        }

    monkeypatch.setattr(profile, "_run", run)
    args = Namespace(
        source=tmp_path / "current",
        compare_source=tmp_path / "before",
        rounds=3,
        size=32,
        batch=8,
        iterations=1,
        smoke=False,
        output=None,
    )
    report = profile._compare(args)
    assert [(name, warm) for name, _cache, warm in observed[:2]] == [
        ("before", True),
        ("current", True),
    ]
    assert all(not warm for _name, _cache, warm in observed[2:])
    assert len({cache for _name, cache, _warm in observed}) == 1
    assert [row["label"] for row in report["warm_imports"]] == ["before", "current"]
    assert "read warm bytecode with writes disabled" in report["bytecode_policy"]
    assert len(report["cold_imports"]) == 24


def test_only_prewarm_processes_can_write_shared_bytecode(tmp_path, monkeypatch):
    environments = []

    def run(_command, *, env, **_kwargs):
        environments.append(env)
        return SimpleNamespace(stdout=json.dumps({"complete": True}))

    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setattr(profile.subprocess, "run", run)
    source = tmp_path / "source"
    cache = tmp_path / "cache"
    command = [profile.sys.executable, "-c", "pass"]
    profile._run(command, source, cache, warm=True)
    profile._run(command, source, cache)
    assert "PYTHONDONTWRITEBYTECODE" not in environments[0]
    assert environments[1]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert {env["PYTHONPYCACHEPREFIX"] for env in environments} == {str(cache)}
    assert {env["PYTHONPATH"] for env in environments} == {str(source)}
    assert {env["PYTHONHASHSEED"] for env in environments} == {"0"}


def test_prewarm_rejects_wrong_source_before_measurement(tmp_path, monkeypatch):
    def run(_command, _source, _cache, *, warm=False):
        assert warm
        return {"imported": str(tmp_path / "wrong/unstd/containers/__init__.py")}

    monkeypatch.setattr(profile, "_run", run)
    args = Namespace(
        source=tmp_path / "current", compare_source=tmp_path / "before", smoke=False
    )
    with pytest.raises(RuntimeError, match="different source"):
        profile._compare(args)


def test_timing_cli_rejects_undersized_or_invalid_measurements():
    with pytest.raises(SystemExit):
        profile._arguments(["--rounds", "1"])
    with pytest.raises(SystemExit):
        profile._arguments(["--batch", "33", "--size", "32"])
    assert profile._arguments(["--rounds", "1", "--smoke"]).smoke
