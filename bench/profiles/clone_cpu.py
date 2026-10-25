"""Checked wall-clock and process CPU profiles for structural cloning.

Run ``PYTHONPATH=src python -m bench.profiles.clone_cpu --output /tmp/clone.json``.
Use ``--compare-dir`` with a saved ``structural.py`` and its matching native
extension to compare the original and current implementations in the same
process. Inputs and checks are outside timing. GC is disabled during batches,
matching the main benchmark harness; ``--gc-enabled`` measures collector cost.
"""

from __future__ import annotations

import argparse
import ast
import copy
import cProfile
import dataclasses
from dataclasses import dataclass
from functools import partial
import gc
import hashlib
import importlib.machinery
import importlib.util
import inspect
import json
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class Record:
    """A default-protocol record containing mutable data."""

    name: str
    values: Any


@dataclass
class Workload:
    """A prepared source and a cycle-aware untimed output check."""

    name: str
    source: Any
    check: Callable[[Any, bool], bool]
    operation: str = "deep"


class Custom:
    """Exercise a user copy protocol beside eligible builtin siblings."""

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, list[int]]:
        """A fresh mutable marker keeps protocol behavior observable."""
        return {"copied": [1, 2]}


def _same(actual: Any, expected: Any) -> bool:
    """Check exact nested types, dictionary order and values independently."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return len(actual) == len(expected) and all(
            _same(key, old_key) and _same(value, old_value)
            for (key, value), (old_key, old_value) in zip(
                actual.items(), expected.items(), strict=True
            )
        )
    if type(expected) in (list, tuple):
        return len(actual) == len(expected) and all(
            _same(value, old) for value, old in zip(actual, expected, strict=True)
        )
    if dataclasses.is_dataclass(expected) and not isinstance(expected, type):
        return all(
            _same(getattr(actual, field.name), getattr(expected, field.name))
            for field in dataclasses.fields(expected)
        )
    return bool(actual == expected)


def _isolated(actual: Any, source: Any, *, reuse_tuples: bool = False) -> bool:
    """Reject source reuse at every mutable branch and structural tuple node."""
    if type(source) is dict:
        return (
            actual is not source
            and len(actual) == len(source)
            and all(
                _isolated(key, old_key, reuse_tuples=reuse_tuples)
                and _isolated(value, old_value, reuse_tuples=reuse_tuples)
                for (key, value), (old_key, old_value) in zip(
                    actual.items(), source.items(), strict=True
                )
            )
        )
    if type(source) in (list, tuple):
        identity_ok = actual is not source or (
            type(source) is tuple and (reuse_tuples or not source)
        )
        return (
            identity_ok
            and len(actual) == len(source)
            and all(
                _isolated(value, old, reuse_tuples=reuse_tuples)
                for value, old in zip(actual, source, strict=True)
            )
        )
    if dataclasses.is_dataclass(source) and not isinstance(source, type):
        state = getattr(source, "__dict__", None)
        actual_state = (
            actual if type(actual) is dict else getattr(actual, "__dict__", None)
        )
        if state is not None and actual_state is state:
            return False
        return actual is not source and all(
            _isolated(
                actual[field.name]
                if type(actual) is dict
                else getattr(actual, field.name),
                getattr(source, field.name),
                reuse_tuples=reuse_tuples,
            )
            for field in dataclasses.fields(source)
        )
    return True


def _workloads() -> list[Workload]:
    deep: Any = [1, "leaf"]
    for _ in range(80):
        deep = [deep]
    shared = [1, 2, 3]
    inputs = [
        ("small-dict", {"id": 7, "name": "small", "ok": True}),
        ("flat-list-1000", list(range(1000))),
        ("flat-dict-1000", {f"field{i}": i for i in range(1000)}),
        ("flat-tuple-1000", tuple(range(1000))),
        ("nested-200", {"rows": [{"id": i, "items": [i, "x"]} for i in range(200)]}),
        ("records-500", [{"id": i, "name": "café", "score": 1.5} for i in range(500)]),
        ("tuple-records-500", tuple((i, "x", (i, i + 1)) for i in range(500))),
        ("wide-lists-1000", [[i] for i in range(1000)]),
        ("deep-80", deep),
        ("large-list-100000", list(range(100_000))),
        ("large-records-20000", [{"id": i, "value": i + 1} for i in range(20_000)]),
        ("repeated-aliases-500", [shared] * 500),
        ("dataclasses-200", [Record("row", [i, i + 1]) for i in range(200)]),
        (
            "dataclass-records-500",
            Record("root", [{"id": i, "values": [i, i + 1]} for i in range(500)]),
        ),
        ("mixed-keys-1000", {i: [i, i + 1] for i in range(1000)}),
        ("foreign-last", [list(range(1000)), Custom()]),
    ]
    cases = []
    for name, source in inputs:
        expected = copy.deepcopy(source)

        def check(
            result: Any, reuse_tuples: bool, old: Any = source, twin: Any = expected
        ) -> bool:
            return _same(result, twin) and _isolated(
                result, old, reuse_tuples=reuse_tuples
            )

        cases.append(Workload(name, source, check))
    for name, source in (
        ("asdict-list-100000", Record("root", list(range(100_000)))),
        (
            "asdict-records-500",
            Record("root", [{"id": i, "values": [i, i + 1]} for i in range(500)]),
        ),
    ):
        expected = dataclasses.asdict(source)

        def check_plain(
            result: Any, reuse_tuples: bool, old: Any = source, twin: Any = expected
        ) -> bool:
            return _same(result, twin) and _isolated(
                result, old, reuse_tuples=reuse_tuples
            )

        cases.append(Workload(name, source, check_plain, operation="asdict"))
    cycle: list[Any] = []
    cycle.append(cycle)
    cases.append(
        Workload(
            "cycle",
            cycle,
            lambda result, reuse: (
                type(result) is list and result is not cycle and result[0] is result
            ),
        )
    )
    return cases


def _callables(case: Workload, modules: dict[str, Any]) -> dict[str, Callable[[], Any]]:
    """Select each operation before measurement, including its stdlib control."""
    calls: dict[str, Callable[[], Any]] = {
        label: partial(getattr(module, case.operation), case.source)
        for label, module in modules.items()
    }
    calls["stdlib"] = partial(
        dataclasses.asdict if case.operation == "asdict" else copy.deepcopy,
        case.source,
    )
    return calls


def _check_result(case: Workload, label: str, result: Any) -> bool:
    """Retain each API's alias and immutable-tuple semantics during checking."""
    if not case.check(result, label == "stdlib"):
        return False
    if case.name == "repeated-aliases-500":
        return (result[0] is result[1]) == (label in ("current", "python", "stdlib"))
    return True


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        message = f"cannot load {path}"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _snapshot(directory: Path, label: str) -> Any:
    module = _load(directory / "structural.py", f"_clone_cpu_{label}_structural")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    imported_entrypoint = next(
        (
            imported.name
            for node in ast.walk(ast.parse((directory / "structural.py").read_text()))
            if isinstance(node, ast.ImportFrom)
            and node.module == "unstd.clone._clone_native"
            for imported in node.names
            if imported.asname == "_native_clone"
        ),
        None,
    )
    entrypoint = manifest.get(
        "native_entrypoint",
        imported_entrypoint or getattr(module._native_clone, "__name__", "clone"),
    )
    if not isinstance(entrypoint, str):
        message = "snapshot native_entrypoint must name a native function"
        raise TypeError(message)
    candidates = [
        directory / ("_clone_native" + suffix)
        for suffix in importlib.machinery.EXTENSION_SUFFIXES
        if (directory / ("_clone_native" + suffix)).is_file()
    ]
    backend = (
        _load(candidates[0], f"_clone_cpu_{label}._clone_native")
        if candidates
        else None
    )
    module._native_clone = getattr(backend, entrypoint) if backend else None
    records_entrypoint = manifest.get("native_records_entrypoint")
    if records_entrypoint is not None and not isinstance(records_entrypoint, str):
        message = "snapshot native_records_entrypoint must name a native function"
        raise TypeError(message)
    module._native_records = (
        getattr(backend, records_entrypoint)
        if backend is not None and records_entrypoint is not None
        else None
    )
    graph_entrypoint = manifest.get("native_graph_entrypoint")
    if graph_entrypoint is not None and not isinstance(graph_entrypoint, str):
        message = "snapshot native_graph_entrypoint must name a native function"
        raise TypeError(message)
    # Loading a saved wrapper can import today's installed backend first. Never
    # let that import make a historical baseline use a newer graph copier.
    module._native_graph = (
        getattr(backend, graph_entrypoint)
        if backend is not None and graph_entrypoint is not None
        else None
    )
    dataclass_entrypoint = manifest.get("native_dataclass_entrypoint")
    if dataclass_entrypoint is not None and not isinstance(dataclass_entrypoint, str):
        message = "snapshot native_dataclass_entrypoint must name a native function"
        raise TypeError(message)
    module._native_dataclass = (
        getattr(backend, dataclass_entrypoint)
        if backend is not None and dataclass_entrypoint is not None
        else None
    )
    return module


def _batch(call: Callable[[], Any], count: int) -> dict[str, float]:
    cpu_start = time.process_time_ns()
    start = time.perf_counter_ns()
    for _ in range(count):
        call()
    wall = time.perf_counter_ns() - start
    cpu = time.process_time_ns() - cpu_start
    return {"wall_ns": wall / count, "cpu_ns": cpu / count}


def _calibrate(call: Callable[[], Any]) -> int:
    count = 1
    while count < 1 << 20:
        elapsed = _batch(call, count)["wall_ns"] * count
        if elapsed >= 5_000_000:
            return count
        count *= min(64, max(2, int(5_000_000 / max(elapsed, 1))))
    return count


def _timings(
    calls: dict[str, Callable[[], Any]], rounds: int, gc_enabled: bool
) -> dict[str, Any]:
    saved_gc = gc.isenabled()
    if gc_enabled:
        gc.enable()
    else:
        gc.disable()
    try:
        counts = {label: _calibrate(call) for label, call in calls.items()}
        samples: dict[str, list[dict[str, float]]] = {label: [] for label in calls}
        for repetition in range(rounds):
            labels = list(calls)[:: 1 if repetition % 2 == 0 else -1]
            for label in labels:
                samples[label].append(_batch(calls[label], counts[label]))
    finally:
        if saved_gc:
            gc.enable()
        else:
            gc.disable()
        gc.collect()
    return {
        label: {
            "iterations": counts[label],
            "samples": values,
            "min_wall_ns": min(row["wall_ns"] for row in values),
            "median_wall_ns": statistics.median(row["wall_ns"] for row in values),
            "min_cpu_ns": min(row["cpu_ns"] for row in values),
            "median_cpu_ns": statistics.median(row["cpu_ns"] for row in values),
        }
        for label, values in samples.items()
    }


def _profile(call: Callable[[], Any]) -> list[dict[str, Any]]:
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(3):
        call()
    profiler.disable()
    rows: list[dict[str, Any]] = []
    for entry in profiler.getstats():
        code = entry.code
        name = (
            f"{code.co_filename}:{code.co_firstlineno}:{code.co_name}"
            if inspect.iscode(code)
            else str(code)
        )
        rows.append(
            {
                "function": name,
                "calls": entry.callcount,
                "self_ns": entry.inlinetime * 1e9,
                "cumulative_ns": entry.totaltime * 1e9,
            }
        )
    return sorted(rows, key=lambda row: row["cumulative_ns"], reverse=True)[:15]


def _native_route(module: Any, source: Any) -> str:
    if module._native_clone is None:
        return "unavailable"
    graph = getattr(module, "_native_graph", None)
    try:
        result = (
            graph(source, {}) if graph is not None else module._native_clone(source)
        )
    except RecursionError:
        return "cycle-deepcopy"
    return "accepted" if result is not NotImplemented else "unsupported"


def _metadata(module: Any) -> dict[str, Any]:
    source = Path(module.__file__ or "")
    native = module._native_clone
    native_sources = [source.parent / "adapter.c", source.parent / "_clone_native.c"]
    if len(source.parents) >= 4:
        native_sources.extend(
            source.parents[3] / "native/clone/src" / name
            for name in ("adapter.c", "_clone_native.c")
        )
    native_source = next(
        (path for path in native_sources if path.is_file()), native_sources[0]
    )
    native_path = sys.modules[native.__module__].__file__ if native else None
    extension = Path(native_path) if native_path else None
    graph = getattr(module, "_native_graph", None)
    native_dataclass = getattr(module, "_native_dataclass", None)
    records = getattr(module, "_native_records", None)
    records_path = sys.modules[records.__module__].__file__ if records else None
    records_extension = Path(records_path) if records_path else None
    snapshot_manifest = source.parent / "manifest.json"
    return {
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "snapshot_manifest_sha256": hashlib.sha256(
            snapshot_manifest.read_bytes()
        ).hexdigest()
        if snapshot_manifest.is_file()
        else None,
        "native_source_sha256": hashlib.sha256(native_source.read_bytes()).hexdigest()
        if native_source.is_file()
        else None,
        "native": str(extension) if extension else None,
        "native_entrypoint": getattr(native, "__name__", None),
        "native_graph_entrypoint": getattr(graph, "__name__", None),
        "native_dataclass_entrypoint": getattr(native_dataclass, "__name__", None),
        "native_sha256": hashlib.sha256(extension.read_bytes()).hexdigest()
        if extension
        else None,
        "native_records": {
            "entrypoint": records.__name__,
            "module": records.__module__,
            "path": str(records_extension),
            "source_sha256": hashlib.sha256(native_source.read_bytes()).hexdigest()
            if native_source.is_file()
            else None,
            "sha256": hashlib.sha256(records_extension.read_bytes()).hexdigest(),
        }
        if records is not None and records_extension is not None
        else None,
    }


def main() -> None:
    """Measure checked, prepared sources across current, Python and stdlib paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--gc-enabled", action="store_true")
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    from unstd.clone import structural as current

    python_only = _load(Path(current.__file__), "_clone_cpu_python_structural")
    python_only._native_clone = None
    python_only._native_graph = None
    python_only._native_records = None
    python_only._native_dataclass = None
    modules = {"current": current, "python": python_only}
    if args.compare_dir:
        modules["before"] = _snapshot(args.compare_dir.resolve(), "before")
    cases = _workloads()
    output: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "gc_enabled_during_batches": args.gc_enabled,
        "gc_threshold": gc.get_threshold(),
        "rounds": args.rounds,
        "includes_output_destruction": True,
        "implementations": {
            label: _metadata(module) for label, module in modules.items()
        },
        "workloads": {},
    }
    for case in cases:
        calls = _callables(case, modules)
        for label, call in calls.items():
            if not _check_result(case, label, call()):
                message = f"incorrect output: {case.name}/{label}"
                raise AssertionError(message)
        timings = _timings(calls, args.rounds, args.gc_enabled)
        for values in timings.values():
            values["cpu_speedup_vs_stdlib"] = (
                timings["stdlib"]["median_cpu_ns"] / values["median_cpu_ns"]
            )
            if "before" in timings:
                values["cpu_speedup_vs_before"] = (
                    timings["before"]["median_cpu_ns"] / values["median_cpu_ns"]
                )
        output["workloads"][case.name] = {
            "operation": case.operation,
            "timings": timings,
            "root_native_probe": {
                label: _native_route(module, case.source)
                for label, module in modules.items()
            },
            "profiles": {label: _profile(call) for label, call in calls.items()},
        }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
