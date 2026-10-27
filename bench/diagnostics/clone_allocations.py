"""A favorable empty-node allocation control beside public clone operations.

Build ``clone_alloc_floor.c`` as ``_clone_alloc_floor`` with the selected Python
interpreter, then pass its extension path using ``--module-path``. The control
allocates empty list/dict/record nodes only, excluding input traversal, payload
references and hash tables. It is a diagnostic comparison, not a formal lower
bound or a production copy algorithm. All actual clone timings call the public
Python wrappers and include output destruction. GC is disabled unless
``--gc-enabled`` is selected. Consumer controls use exact record types but omit
nested data copying and cannot establish a formal minimum clone latency.
"""

from __future__ import annotations

import argparse
import dataclasses
from fnmatch import fnmatchcase
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
from typing import TYPE_CHECKING, Any

from bench.profiles import clone_common_cpu as common
from bench.profiles import clone_cpu as base


if TYPE_CHECKING:
    from collections.abc import Callable


def _record_controls(
    source: object,
) -> tuple[tuple[type[object], ...], dict[str, Callable[[], object]]]:
    """Prepare exact record allocation and intentionally shallow state controls."""
    from pydantic import BaseModel

    from bench.workloads.clone_consumers import _children

    seen: set[int] = set()
    records: list[object] = []

    def visit(value: object) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, BaseModel) or (
            dataclasses.is_dataclass(value) and not isinstance(value, type)
        ):
            records.append(value)
        for child in _children(value):
            visit(child)

    visit(source)
    kinds = tuple(type(record) for record in records)
    plans: list[tuple[type[object], tuple[tuple[str, object, bool], ...]]] = []
    for record in records:
        attributes: tuple[tuple[str, object, bool], ...]
        if isinstance(record, BaseModel):
            attributes = tuple(
                (name, getattr(record, name), True)
                for name in (
                    "__dict__",
                    "__pydantic_extra__",
                    "__pydantic_private__",
                    "__pydantic_fields_set__",
                )
            )
        elif hasattr(record, "__dict__"):
            attributes = (("__dict__", record.__dict__, True),)
        elif dataclasses.is_dataclass(record) and not isinstance(record, type):
            attributes = tuple(
                (field.name, getattr(record, field.name), False)
                for field in dataclasses.fields(record)
            )
        else:
            message = "Allocation control received an unsupported record"
            raise TypeError(message)
        plans.append((type(record), attributes))

    def empty_records() -> list[object]:
        return [object.__new__(kind) for kind in kinds]

    def shallow_states() -> list[object]:
        output: list[object] = []
        for kind, attributes in plans:
            twin: object = object.__new__(kind)
            for name, value, copy_state in attributes:
                state = (
                    value.copy()
                    if copy_state and isinstance(value, dict | set)
                    else value
                )
                object.__setattr__(twin, name, state)
            output.append(twin)
        return output

    return kinds, {
        "exact_record_new_control": empty_records,
        "shallow_record_state_control": shallow_states,
    }


def main() -> None:
    """Measure public cloning beside prepared counts of empty output nodes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-path", type=Path, required=True)
    parser.add_argument("--compare-dir", type=Path)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--infer-native-bindings", action="store_true")
    parser.add_argument("--consumer-cases", action="store_true")
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--gc-enabled", action="store_true")
    parser.add_argument("--target-speedup", type=float, default=100)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    if not math.isfinite(args.target_speedup) or args.target_speedup < 1:
        parser.error("target-speedup must be finite and at least 1")
    from unstd.clone import structural as current

    if args.source_dir is not None:
        current = common._snapshot(
            args.source_dir.resolve(),
            "allocation_current",
            infer_native_bindings=args.infer_native_bindings,
        )

    extension = args.module_path.resolve()
    control = base._load(extension, "_clone_alloc_floor")
    modules = {"current": current}
    if args.compare_dir:
        modules["before"] = common._snapshot(
            args.compare_dir.resolve(),
            "alloc_before",
            infer_native_bindings=args.infer_native_bindings,
        )
    source = Path(__file__).with_name("clone_alloc_floor.c")
    output: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "rounds": args.rounds,
        "gc_enabled_during_batches": args.gc_enabled,
        "diagnostic": "favorable empty-node allocation control; no formal lower bound",
        "control_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "control_extension_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "timing_helper_sha256": hashlib.sha256(
            Path(base.__file__).read_bytes()
        ).hexdigest(),
        "workload_helper_sha256": hashlib.sha256(
            Path(common.__file__).read_bytes()
        ).hexdigest(),
        "implementations": {
            label: base._metadata(module) for label, module in modules.items()
        },
        "workloads": {},
        "target_speedup": args.target_speedup,
        "control_limits": {
            "empty_node_control": "C allocation only; empty lists/dicts and exact record types; no payload, traversal, populated tables, tuple or set allocations.",
            "exact_record_new_control": "Python object.__new__ and output-list destruction only; no record state or nested containers.",
            "shallow_record_state_control": "Fresh record state dicts/sets only; all nested payloads remain shared; not a valid clone.",
        },
    }
    selected = {
        "nested-200",
        "records-500",
        "dataclasses-200",
        "dataclass-records-500",
        "asdict-records-500",
    }
    cases = common._workloads()
    if args.consumer_cases:
        from bench.workloads import clone_consumers

        consumer_cases = clone_consumers.workloads()
        cases.extend(consumer_cases)
        selected.update(case.name for case in consumer_cases)
        output["consumer_workload_sha256"] = hashlib.sha256(
            Path(clone_consumers.__file__).read_bytes()
        ).hexdigest()
    cases = (
        [
            case
            for case in cases
            if any(fnmatchcase(case.name, pattern) for pattern in args.include)
            if args.include
        ]
        if args.include
        else [case for case in cases if case.name in selected]
    )
    if not cases:
        parser.error("include patterns selected no allocation workloads")
    for case in cases:
        counts = common._node_counts(case.source, plain=case.operation == "asdict")
        calls = base._callables(case, modules)
        for label, call in calls.items():
            if not common._check(case, label, call()):
                message = f"incorrect public output: {case.name}/{label}"
                raise AssertionError(message)
        record_types: type[object] | tuple[type[object], ...] = base.Record
        if args.consumer_cases:
            kinds, record_calls = _record_controls(case.source)
            if case.operation == "asdict":
                kinds = ()
            else:
                calls.update(record_calls)
            record_types = kinds
        calls["empty_node_control"] = partial(
            control.nodes,
            counts["lists"],
            counts["dicts"],
            counts["records"],
            record_types,
        )
        nodes = calls["empty_node_control"]()
        if len(nodes) != counts["lists"] + counts["dicts"] + counts["records"]:
            message = f"wrong empty-node count: {case.name}"
            raise AssertionError(message)
        if (
            isinstance(record_types, tuple)
            and record_types
            and any(
                type(node) is not kind
                for node, kind in zip(
                    nodes[-len(record_types) :], record_types, strict=True
                )
            )
        ):
            message = f"wrong exact record types: {case.name}"
            raise AssertionError(message)
        del nodes
        timings = base._timings(calls, args.rounds, args.gc_enabled)
        stdlib_target = timings["stdlib"]["median_cpu_ns"] / args.target_speedup
        output["workloads"][case.name] = {
            "node_counts": counts,
            "timings": timings,
            "stdlib_cpu_target_ns": stdlib_target,
            "allocation_controls_over_target": {
                label: values["median_cpu_ns"] / stdlib_target
                for label, values in timings.items()
                if label.endswith("_control")
            },
        }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
