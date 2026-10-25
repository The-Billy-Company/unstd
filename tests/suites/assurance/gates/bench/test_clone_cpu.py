"""The CPU evidence driver rejects cheap but incorrect copies and wrong baselines."""

from __future__ import annotations

import copy
import hashlib
import importlib.machinery
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from bench.profiles import clone_cpu as profile


if TYPE_CHECKING:
    from pathlib import Path


class ListSubclass(list):
    pass


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ({"value": [True]}, {"value": [1]}),
        ({True: "value"}, {1: "value"}),
        ({"value": ListSubclass([1])}, {"value": [1]}),
        ({"value": (1,)}, {"value": [1]}),
        ({"second": 2, "first": 1}, {"first": 1, "second": 2}),
    ],
)
def test_exact_output_check_rejects_equal_values_with_wrong_shape(actual, expected):
    assert actual == expected or isinstance(actual["value"], tuple)
    assert not profile._same(actual, expected)


def test_mutable_isolation_check_rejects_nested_shallow_copy():
    source = {"rows": [{"items": [1, 2]}]}
    assert not profile._isolated(source.copy(), source)
    assert not profile._isolated({"rows": [source["rows"][0].copy()]}, source)
    assert profile._isolated(copy.deepcopy(source), source)


def test_immutable_tuple_reuse_is_only_accepted_for_stdlib_control():
    source = (1, (2, 3))
    assert not profile._isolated(source, source)
    assert profile._isolated(source, source, reuse_tuples=True)
    assert profile._isolated((), ())
    shared = [1, 2]
    nested = (shared,)
    assert not profile._isolated(nested, nested, reuse_tuples=True)


def test_prepared_workloads_enforce_alias_semantics():
    case = next(
        case for case in profile._workloads() if case.name == "repeated-aliases-500"
    )
    shared_result = copy.deepcopy(case.source)
    distinct_result = [list(branch) for branch in case.source]
    for label in ("current", "python", "stdlib"):
        assert profile._check_result(case, label, shared_result)
        assert not profile._check_result(case, label, distinct_result)
    for label in ("before", "original"):
        assert profile._check_result(case, label, distinct_result)
        assert not profile._check_result(case, label, shared_result)


@pytest.mark.parametrize("operation", ["deep", "asdict"])
def test_operation_dispatch_selects_matching_implementation_and_stdlib(operation):
    source = profile.Record("root", [1, 2])
    module = SimpleNamespace(
        deep=lambda value: ("deep", value),
        asdict=lambda value: ("asdict", value),
    )
    case = profile.Workload("dispatch", source, lambda value, reuse: True, operation)
    calls = profile._callables(case, {"current": module})
    assert calls["current"]() == (operation, source)
    control = calls["stdlib"]()
    if operation == "asdict":
        assert type(control) is dict
        assert control == {"name": "root", "values": [1, 2]}
    else:
        assert type(control) is profile.Record
        assert control is not source
        assert control == source
    assert profile._isolated(control, source, reuse_tuples=True)


@pytest.mark.parametrize("c_name", ["adapter.c", "_clone_native.c"])
def test_snapshot_selects_matching_abi_backend_and_records_saved_source_hash(
    tmp_path, monkeypatch, c_name
):
    source = tmp_path / "structural.py"
    source.write_text("_native_clone = None\n")
    c_source = tmp_path / c_name
    c_source.write_text("/* saved source */\n")
    selected = tmp_path / ("_clone_native" + importlib.machinery.EXTENSION_SUFFIXES[0])
    selected.write_bytes(b"matching ABI")
    (tmp_path / "_clone_native.cpython-999-other.so").write_bytes(b"wrong ABI")
    wrapper = SimpleNamespace(__file__=str(source), _native_clone=None)
    loads = []

    def backend(value):
        return value

    backend.__module__ = "_clone_cpu_test_backend"
    monkeypatch.setitem(
        sys.modules, backend.__module__, SimpleNamespace(__file__=str(selected))
    )

    def load(path: Path, name: str):
        loads.append((path, name))
        return wrapper if path == source else SimpleNamespace(clone=backend)

    monkeypatch.setattr(profile, "_load", load)
    actual = profile._snapshot(tmp_path, "test_before")
    assert actual._native_clone is backend
    assert [path for path, _name in loads] == [source, selected]
    assert loads[1][1] == "_clone_cpu_test_before._clone_native"
    metadata = profile._metadata(actual)
    assert metadata["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (
        metadata["native_source_sha256"]
        == hashlib.sha256(c_source.read_bytes()).hexdigest()
    )
    assert (
        metadata["native_sha256"] == hashlib.sha256(selected.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("manifest", [False, True])
def test_snapshot_retains_checked_cycle_entrypoint(tmp_path, monkeypatch, manifest):
    (tmp_path / "structural.py").write_text("_native_clone = None\n")
    selected = tmp_path / ("_clone_native" + importlib.machinery.EXTENSION_SUFFIXES[0])
    selected.write_bytes(b"matching ABI")

    def clone_checked(value):
        message = "proved cycle"
        raise RecursionError(message)

    wrapper = SimpleNamespace(_native_clone=clone_checked)
    if manifest:
        (tmp_path / "manifest.json").write_text(
            '{"native_entrypoint": "clone_checked"}\n'
        )
        wrapper._native_clone = None

    def load(path: Path, name: str):
        if path.name == "structural.py":
            return wrapper
        return SimpleNamespace(
            clone=lambda value: NotImplemented, clone_checked=clone_checked
        )

    monkeypatch.setattr(profile, "_load", load)
    actual = profile._snapshot(tmp_path, "checked_before")
    assert actual._native_clone is clone_checked
    assert profile._native_route(actual, []) == "cycle-deepcopy"


@pytest.mark.parametrize("include_records", [False, True])
def test_snapshot_records_kernel_never_leaks_from_current_import(
    tmp_path, monkeypatch, include_records
):
    (tmp_path / "structural.py").write_text("_native_clone = None\n")
    selected = tmp_path / ("_clone_native" + importlib.machinery.EXTENSION_SUFFIXES[0])
    selected.write_bytes(b"matching ABI")
    if include_records:
        (tmp_path / "manifest.json").write_text(
            '{"native_entrypoint": "clone", '
            '"native_records_entrypoint": "clone_records"}\n'
        )

    def saved_records(source, cls):
        return []

    wrapper = SimpleNamespace(_native_clone=None, _native_records=lambda: "leaked")
    backend = SimpleNamespace(clone=lambda value: value, clone_records=saved_records)
    monkeypatch.setattr(
        profile,
        "_load",
        lambda path, name: wrapper if path.name == "structural.py" else backend,
    )
    actual = profile._snapshot(tmp_path, "record_before")
    assert actual._native_records is (saved_records if include_records else None)


def test_snapshot_reads_entrypoint_when_current_native_import_is_unavailable(
    tmp_path, monkeypatch
):
    (tmp_path / "structural.py").write_text(
        "from unstd.clone._clone_native import clone_checked as _native_clone\n"
    )
    selected = tmp_path / ("_clone_native" + importlib.machinery.EXTENSION_SUFFIXES[0])
    selected.write_bytes(b"matching ABI")
    wrapper = SimpleNamespace(_native_clone=None)

    def checked(value):
        return value

    backend = SimpleNamespace(clone=lambda value: NotImplemented, clone_checked=checked)
    monkeypatch.setattr(
        profile,
        "_load",
        lambda path, name: wrapper if path.name == "structural.py" else backend,
    )
    assert profile._snapshot(tmp_path, "missing_current")._native_clone is checked


@pytest.mark.parametrize("kernel", ["graph", "dataclass"])
@pytest.mark.parametrize("include_kernel", [False, True])
def test_snapshot_optional_kernel_uses_only_the_saved_backend(
    tmp_path, monkeypatch, kernel, include_kernel
):
    (tmp_path / "structural.py").write_text("_native_clone = None\n")
    selected = tmp_path / ("_clone_native" + importlib.machinery.EXTENSION_SUFFIXES[0])
    selected.write_bytes(b"matching ABI")
    if include_kernel:
        (tmp_path / "manifest.json").write_text(
            f'{{"native_{kernel}_entrypoint": "clone_{kernel}"}}\n'
        )

    def saved_kernel(*args):
        return []

    wrapper = SimpleNamespace(
        _native_clone=None, **{f"_native_{kernel}": lambda: "leaked"}
    )
    backend = SimpleNamespace(
        clone=lambda value: value, **{f"clone_{kernel}": saved_kernel}
    )
    monkeypatch.setattr(
        profile,
        "_load",
        lambda path, name: wrapper if path.name == "structural.py" else backend,
    )
    actual = profile._snapshot(tmp_path, f"{kernel}_before")
    assert getattr(actual, f"_native_{kernel}") is (
        saved_kernel if include_kernel else None
    )
