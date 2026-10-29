"""Clone/UID performance evidence must reject invalid shortcuts and coverage gaps."""

from __future__ import annotations

from collections import defaultdict
import sys

import pytest

from bench.profiles.clone_ids_profile import (
    Record,
    _bundle,
    _fork_reset_control,
    _isolated,
    _load,
    _profile,
    _same,
    _workloads,
)
from tests.support.environment.anchors import REPO_ROOT


@pytest.mark.parametrize("value", [[1], {"nested": [1]}, Record("record", [1])])
def test_output_check_rejects_reused_mutable_sources(value: object) -> None:
    assert _same(value, value)
    assert not _isolated(value, value)


def test_output_check_rejects_shallow_nested_clones() -> None:
    source = {"nested": [1]}
    assert not _isolated(source.copy(), source)
    assert _isolated({"nested": [1]}, source)


def test_value_check_observes_key_types_order_and_default_factory() -> None:
    assert not _same({True: "value"}, {1: "value"})
    assert not _same({"b": 2, "a": 1}, {"a": 1, "b": 2})
    assert not _same(defaultdict(list, a=1), defaultdict(set, a=1))


def test_stdlib_profile_records_coverage_and_explicit_optional_skips() -> None:
    result = _bundle(REPO_ROOT / "src", "gate", stdlib_only=True)
    skipped = {row["function"] for row in result["functions"] if row["skipped"]}
    expected_skips = {
        "structural._model",
        "structural._model_private",
        "structural._reduced",
    }
    from unstd.clone import structural

    if structural._native_walk_graph is None or not (3, 12) <= sys.version_info[:2] < (
        3,
        15,
    ):
        expected_skips.add("structural._record_admit")
    assert skipped == expected_skips
    assert all(row["calls"] or row["skipped"] for row in result["functions"])
    locations = {
        (row["function"].split(".")[0], row["line"]) for row in result["functions"]
    }
    assert len(locations) == len(result["functions"])
    assert {row["function"] for row in result["aliases"]} == {
        "clone.copy",
        "clone.deepcopy",
        "uid.parse",
        "uid.derive",
    }
    assert all(row["calls"] for row in result["aliases"])
    calls = {row["function"]: row["calls"] for row in result["functions"]}
    assert calls["structural._walk_data"]
    assert calls["structural._plain_field"]
    assert calls["uid.new"] == 6
    assert calls["uid.new_hex"] == 6
    assert calls["uid._next_ms_and_counter"] >= 21
    assert calls["uid._reset_after_fork"] == 3


def test_python_only_profile_disables_secondary_native_kernel_and_metadata() -> None:
    source = REPO_ROOT / "src"
    clone = _load(source, "clone/structural.py", "_gate_python_clone")
    uid = _load(source, "ids/uid.py", "_gate_python_uid")
    fallback = _load(source, "ids/uid.py", "_gate_python_fallback")
    clone._native_clone = None
    clone._native_graph = None
    clone._native_records = None
    clone._native_record_graph = None
    clone._native_walk_graph = None
    clone._native_dataclass = None
    clone._RECORDS.add(Record)
    cases = _workloads(clone, uid, fallback, stdlib_only=True)
    result = _profile([clone, uid], cases, stdlib_only=True)
    record_selector = next(
        row
        for row in result["functions"]
        if row["function"] == "structural._record_admit"
    )
    assert record_selector["calls"] == 0
    assert record_selector["skipped"] == "native general walk unavailable"
    assert Record not in clone._RECORDS


def test_reset_profile_control_observes_real_lock_state_and_rejects_noop(
    monkeypatch,
) -> None:
    uid = _load(REPO_ROOT / "src", "ids/uid.py", "_gate_reset_uid")
    control = _fork_reset_control(uid)
    assert control is not None
    backend = uid._uuid7
    assert control.check(control.call())
    assert uid._uuid7 is backend
    monkeypatch.setattr(uid, "_reset_after_fork", lambda: None)
    assert not control.check(control.call())


def test_reset_profile_control_is_absent_for_legacy_sources() -> None:
    class Legacy:
        pass

    assert _fork_reset_control(Legacy()) is None


def test_new_unexercised_runtime_function_fails_profile() -> None:
    source = REPO_ROOT / "src"
    clone = _load(source, "clone/structural.py", "_gate_clone")
    uid = _load(source, "ids/uid.py", "_gate_uid")
    fallback = _load(source, "ids/uid.py", "_gate_fallback")
    cases = _workloads(clone, uid, fallback, stdlib_only=True)

    def unexercised() -> None:
        pass

    unexercised.__module__ = clone.__name__
    clone.unexercised = unexercised
    with pytest.raises(AssertionError, match=r"structural\.unexercised"):
        _profile([clone, uid], cases, stdlib_only=True)
