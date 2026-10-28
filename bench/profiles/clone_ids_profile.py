"""Complete clone/UID function profiles and paired, output-checked timings.

Run ``python -m bench.clone_ids_profile --output /tmp/clone-ids.json``.
Use ``--compare-source /path/to/before/src`` to compare saved source files.
The profiler runs separately from timing; before/after timing order alternates.
Optional model workloads require pydantic and msgspec unless ``--stdlib-only``
is selected. The handwritten UUID fallback is always exercised, even on 3.14+.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, namedtuple
import copy
import cProfile
import dataclasses
from dataclasses import dataclass
import datetime
import enum
from functools import partial
import hashlib
from importlib.metadata import PackageNotFoundError, version
import importlib.util
import inspect
import json
from pathlib import Path
import platform
import statistics
import sys
from typing import TYPE_CHECKING, Any
import uuid


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


if TYPE_CHECKING or __package__:
    from bench.core.timing import measure
else:
    # Direct execution works without importing the other benchmark groups and
    # their optional dependencies through bench.__init__.
    from bench.core.timing import measure


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class Workload:
    """A timed operation and its untimed, independent output check."""

    name: str
    call: Callable[[], Any]
    check: Callable[[Any], bool]


@dataclass
class Record:
    """Nested data exercises both cloning and dataclass conversion."""

    name: str
    values: Any


@dataclass
class InheritedRecord(Record):
    """Inherited layouts retain the Python dataclass reconstruction recipe."""


@dataclass(slots=True)
class Slotted:
    """Slotted records follow the general copy protocol."""

    values: list[int]


class Choice(enum.Enum):
    """Enums retain their singleton identity."""

    FIRST = 1


class Custom:
    """A user-defined copy protocol must remain observable."""

    def __deepcopy__(self, memo: Any) -> dict[str, str]:
        """Return an explicit marker instead of reconstructing this object."""
        return {"copied": "custom"}


def _same(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return (
            len(actual) == len(expected)
            and (
                not isinstance(expected, defaultdict)
                or _same(actual.default_factory, expected.default_factory)
            )
            and all(
                _same(key, expected_key) and _same(value, expected_value)
                for (key, value), (expected_key, expected_value) in zip(
                    actual.items(), expected.items(), strict=True
                )
            )
        )
    if isinstance(expected, list | tuple):
        return len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected, strict=True)
        )
    if dataclasses.is_dataclass(expected) and not isinstance(expected, type):
        return all(
            _same(getattr(actual, field.name), getattr(expected, field.name))
            for field in dataclasses.fields(expected)
        ) and (
            not hasattr(expected, "__dict__")
            or _same(actual.__dict__, expected.__dict__)
        )
    return bool(actual == expected)


def _isolated(actual: Any, original: Any) -> bool:
    """Reject shared mutable branches without requiring new immutable leaves."""
    if isinstance(original, dict):
        return actual is not original and all(
            _isolated(key, old_key) and _isolated(value, old_value)
            for (key, value), (old_key, old_value) in zip(
                actual.items(), original.items(), strict=True
            )
        )
    if isinstance(original, list | tuple):
        return (not isinstance(original, list) or actual is not original) and all(
            _isolated(value, old_value)
            for value, old_value in zip(actual, original, strict=True)
        )
    if isinstance(original, set | bytearray):
        return actual is not original
    if dataclasses.is_dataclass(original) and not isinstance(original, type):
        if isinstance(actual, dict):
            return all(
                _isolated(actual[field.name], getattr(original, field.name))
                for field in dataclasses.fields(original)
            )
        if actual is original:
            return False
        if hasattr(original, "__dict__"):
            return _isolated(actual.__dict__, original.__dict__)
        return all(
            _isolated(getattr(actual, field.name), getattr(original, field.name))
            for field in dataclasses.fields(original)
        )
    if hasattr(original, "__pydantic_private__"):
        return actual is not original and all(
            _isolated(getattr(actual, name), getattr(original, name))
            for name in ("__dict__", "__pydantic_extra__", "__pydantic_private__")
        )
    if hasattr(original, "__struct_fields__"):
        return actual is not original and all(
            _isolated(getattr(actual, name), getattr(original, name))
            for name in original.__struct_fields__
        )
    return True


def _load(source: Path, relative: str, name: str) -> Any:
    path = source / "unstd" / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"cannot load {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if module.__file__ is None or Path(module.__file__).resolve() != path.resolve():
        msg = f"source mismatch for {path}"
        raise AssertionError(msg)
    return module


def _workloads(
    clone: Any, uid: Any, fallback: Any, stdlib_only: bool
) -> list[Workload]:
    cases: list[Workload] = []

    def add(
        name: str, call: Callable[[], Any], expected: Any, original: Any = None
    ) -> None:
        cases.append(
            Workload(
                name,
                call,
                lambda actual: _same(actual, expected) and _isolated(actual, original),
            )
        )

    inputs: list[tuple[str, Any]] = [
        ("atom", "hello"),
        ("small", {"id": 7, "name": "small", "ok": True}),
        ("mixed-keys", {1: [2], ("a", 3): {4: "four"}}),
        ("nested200", {"rows": [{"id": i, "items": [i, "x"]} for i in range(200)]}),
        ("records500", [{"id": i, "name": "café", "score": 1.5} for i in range(500)]),
        ("dataclass", Record("root", [Record("child", [1, 2])])),
        ("dataclasses200", [Record("row", [i, i + 1]) for i in range(200)]),
        ("dataclass-inherited", InheritedRecord("child", [1, 2])),
        ("slotted", Slotted([1, 2])),
        ("enum", Choice.FIRST),
        ("custom", Custom()),
        ("bytearray", bytearray(b"abc")),
        ("datetime", datetime.datetime(2026, 10, 18, tzinfo=datetime.UTC)),
        ("defaultdict", defaultdict(list, a=[1, 2])),
    ]
    for container in (list, tuple, set, frozenset):
        inputs.extend(
            (f"{container.__name__}{size}", container(range(size)))
            for size in (0, 8, 1000)
        )
    wide_record = Record("wide", [1, 2])
    wide_record.__dict__.update({f"field_{i}": i for i in range(200)})
    inputs.append(("dataclass-wide200", wide_record))
    for name, value in inputs:
        add(
            f"clone.deep/{name}",
            partial(clone.deep, value),
            copy.deepcopy(value),
            value,
        )
    cold_record = Record("cold", [1, 2])

    def cold_deep() -> Any:
        clone._NODES.pop(Record, None)
        return clone.deep(cold_record)

    def cold_asdict() -> Any:
        clone._FIELDS.pop(Record, None)
        return clone.asdict(cold_record)

    add("clone.deep/cold-dataclass", cold_deep, copy.deepcopy(cold_record), cold_record)
    add(
        "clone.asdict/cold-fields",
        cold_asdict,
        dataclasses.asdict(cold_record),
        cold_record,
    )
    shared: list[int] = [1]
    shared_input = [shared, shared]
    # Saved wrappers predate the per-call memo. Validate their historical
    # contract without accepting alias splitting from the current walker.
    preserves_aliases = "memo" in inspect.signature(clone._walk).parameters
    cases.append(
        Workload(
            "clone.deep/shared",
            partial(clone.deep, shared_input),
            lambda result: (
                _same(result, shared_input)
                and (result[0] is result[1]) == preserves_aliases
                and result[0] is not shared
            ),
        )
    )
    cycle: list[Any] = []
    cycle.append(cycle)
    cases.append(
        Workload(
            "clone.deep/cycle",
            partial(clone.deep, cycle),
            lambda result: result is not cycle and result[0] is result,
        )
    )
    point_type = namedtuple("point_type", "x y")
    point = point_type(1, [2])
    for name, value in (
        ("small", Record("root", [1, 2])),
        ("records200", Record("root", [Record("child", [i]) for i in range(200)])),
        ("mixed-keys", Record("root", {1: [2], ("a", 3): {4: "four"}})),
        ("namedtuple", Record("root", point)),
        ("defaultdict", Record("root", defaultdict(list, a=[1, 2]))),
        ("custom-leaf", Record("root", Custom())),
    ):
        add(
            f"clone.asdict/{name}",
            partial(clone.asdict, value),
            dataclasses.asdict(value),
            value,
        )
    record = Record("root", [1, 2])
    add(
        "clone.asdict/factory",
        partial(clone.asdict, record, dict_factory=list),
        dataclasses.asdict(record, dict_factory=list),
    )
    for name, call in (("copy", copy.copy), ("deepcopy", copy.deepcopy)):
        actual = getattr(clone, name)
        if actual is not call:
            msg = f"clone.{name} must remain a direct stdlib alias"
            raise AssertionError(msg)
        add(f"clone.{name}/record", partial(actual, record), call(record))
    if not stdlib_only:
        import msgspec
        import pydantic

        class Model(pydantic.BaseModel):
            """A model with fields, extra state, and private state."""

            model_config = pydantic.ConfigDict(extra="allow")
            values: list[int]
            _private: Any = pydantic.PrivateAttr(default_factory=lambda: [1, 2])

        class Struct(msgspec.Struct):
            """The native reducer rebuilds this record."""

            values: list[int]

        models = [
            Model.model_validate({"values": [i], "extra": [i + 1]}) for i in range(40)
        ]
        structs = [Struct([i]) for i in range(40)]
        add(
            "clone.deep/models40",
            partial(clone.deep, models),
            copy.deepcopy(models),
            models,
        )
        private_model = Model(values=[1, 2])
        private_model.__pydantic_private__ = {
            f"private_{i}": [i, i + 1] for i in range(100)
        }
        add(
            "clone.deep/model-private100",
            partial(clone.deep, private_model),
            copy.deepcopy(private_model),
            private_model,
        )
        add(
            "clone.deep/structs40",
            partial(clone.deep, structs),
            copy.deepcopy(structs),
            structs,
        )

    if uid.parse is not uuid.UUID or uid.derive is not uuid.uuid5:
        msg = "UID parse and derive must remain direct stdlib aliases"
        raise AssertionError(msg)
    fallback._uuid7 = fallback._stdlib_uuid7
    for label, module in (("selected", uid), ("fallback", fallback)):
        for name in ("new", "new_hex"):
            canonical = name == "new"

            def check_uuid(result: Any, hyphens: bool = canonical) -> bool:
                parsed = uuid.UUID(result)
                return (
                    type(result) is str
                    and parsed.version == 7
                    and parsed.variant == uuid.RFC_4122
                    and result == (str(parsed) if hyphens else parsed.hex)
                )

            cases.append(
                Workload(f"uid.{name}/{label}", getattr(module, name), check_uuid)
            )
    cases.append(
        Workload(
            "uid._stdlib_uuid7",
            uid._stdlib_uuid7,
            lambda result: (
                type(result) is uuid.UUID
                and result.version == 7
                and result.variant == uuid.RFC_4122
            ),
        )
    )
    cases.append(
        Workload(
            "uid._seed_counter", uid._seed_counter, lambda result: 0 <= result <= 4095
        )
    )
    # State preparation is deliberately included in this cold-path workload.
    # All ordinary minting workloads above measure the unmodified real clock.
    for branch in ("advance", "same", "backward", "overflow"):
        initial_counter = 4095 if branch == "overflow" else 3
        now = (
            1_700_000_000_000
            + {"advance": 1, "same": 0, "backward": -1, "overflow": 0}[branch]
        )

        def counter(stamp: int = now, count: int = initial_counter) -> tuple[int, int]:
            uid._last_ms = 1_700_000_000_000
            uid._last_counter = count
            result: tuple[int, int] = uid._next_ms_and_counter(stamp)
            return result

        def check_counter(result: Any, selected: str = branch) -> bool:
            stamp, count = result
            expected_ms = 1_700_000_000_000 + (selected in ("advance", "overflow"))
            return bool(
                stamp == expected_ms
                and (
                    0 <= count <= 4095
                    if selected in ("advance", "overflow")
                    else count == 4
                )
            )

        cases.append(
            Workload(f"uid._next_ms_and_counter/{branch}", counter, check_counter)
        )
    text = "886313e1-3b8a-5372-9b90-0c9aee199e5d"
    add("uid.parse", partial(uid.parse, text), uuid.UUID(text))
    add(
        "uid.derive",
        partial(uid.derive, uuid.NAMESPACE_DNS, "python.org"),
        uuid.UUID(text),
    )
    return cases


def _fork_reset_control(uid: Any) -> Workload | None:
    """Check the child-reset cold path without inventing a legacy timing pair.

    Saved comparison sources predate the fork hook. Only implementations that
    provide it receive this profiling control; ordinary paired timing workloads
    retain their identical names and meaning across those source snapshots.
    """
    if not hasattr(uid, "_reset_after_fork"):
        return None

    def reset() -> tuple[bool, bool, bool, bool, Any, Any, bool]:
        inherited = uid._counter_lock
        backend = uid._uuid7
        with inherited:
            uid._last_ms, uid._last_counter = 1_700_000_000_000, 91
            uid._reset_after_fork()
            fresh = uid._counter_lock
            acquired = fresh is not inherited and fresh.acquire(blocking=False)
            if acquired:
                fresh.release()
            return (
                fresh is not inherited,
                inherited.locked(),
                fresh.locked(),
                acquired,
                uid._last_ms,
                uid._last_counter,
                uid._uuid7 is backend,
            )

    return Workload(
        "uid._reset_after_fork/held-lock",
        reset,
        lambda result: result == (True, True, False, True, None, 0, True),
    )


def _profile(
    modules: list[Any], cases: list[Workload], stdlib_only: bool
) -> dict[str, Any]:
    clone = modules[0]
    clone._NODES.clear()
    clone._FIELDS.clear()
    if hasattr(clone, "_RECORDS"):
        clone._RECORDS.clear()
    clone._PYDANTIC_UNDEFINED = None
    reset_control = _fork_reset_control(modules[1])
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        for _ in range(3):
            if reset_control is not None and not reset_control.check(
                reset_control.call()
            ):
                msg = f"incorrect output: {reset_control.name}"
                raise AssertionError(msg)
            for case in cases:
                case.call()
    finally:
        profiler.disable()
    # Native and fallback namespaces have separate code objects at identical
    # source locations. pstats overwrites those duplicate keys; aggregate the
    # raw entries explicitly so both executions contribute to function totals.
    stats: dict[tuple[str, int, str], list[Any]] = {}
    for raw_entry in profiler.getstats():
        code = raw_entry.code
        if not inspect.iscode(code):
            continue
        key = (code.co_filename, code.co_firstlineno, code.co_name)
        totals = stats.setdefault(key, [0, 0, 0.0, 0.0])
        totals[0] += raw_entry.reccallcount
        totals[1] += raw_entry.callcount
        totals[2] += raw_entry.inlinetime
        totals[3] += raw_entry.totaltime
    functions = []
    for module in modules:
        for name, function in vars(module).items():
            if (
                not inspect.isfunction(function)
                or function.__module__ != module.__name__
                or function.__name__ != name
            ):
                continue
            code = function.__code__
            entry = stats.get((code.co_filename, code.co_firstlineno, code.co_name))
            functions.append(
                {
                    "function": f"{Path(module.__file__).stem}.{name}",
                    "line": code.co_firstlineno,
                    "calls": entry[1] if entry else 0,
                    "self_ns": entry[2] * 1e9 if entry else 0,
                    "cumulative_ns": entry[3] * 1e9 if entry else 0,
                    "skipped": (
                        "optional model workload disabled"
                        if stdlib_only
                        and name in ("_model", "_model_private", "_reduced")
                        else "native general walk unavailable"
                        if name == "_record_admit"
                        and getattr(clone, "_native_walk_graph", None) is None
                        else "native record kernel unavailable"
                        if name == "_record_class"
                        and getattr(clone, "_native_records", None) is None
                        else "tree-record selector unused by graph cloning"
                        if name == "_record_class"
                        and "memo" in inspect.signature(clone._walk).parameters
                        else None
                    ),
                }
            )
    missing = [
        row["function"] for row in functions if not row["calls"] and not row["skipped"]
    ]
    if missing:
        msg = f"unprofiled runtime functions: {missing}"
        raise AssertionError(msg)
    uid = modules[1]
    aliases = []
    for name, target in (
        ("clone.copy", clone.copy),
        ("clone.deepcopy", clone.deepcopy),
        ("uid.parse", uid.parse.__init__),
        ("uid.derive", uid.derive),
    ):
        code = target.__code__
        entry = stats[(code.co_filename, code.co_firstlineno, code.co_name)]
        aliases.append(
            {
                "function": name,
                "target": f"{target.__module__}.{target.__qualname__}",
                "calls": entry[1],
                "self_ns": entry[2] * 1e9,
                "cumulative_ns": entry[3] * 1e9,
            }
        )
    return {"functions": functions, "aliases": aliases}


def _bundle(source: Path, label: str, stdlib_only: bool) -> dict[str, Any]:
    clone = _load(source, "clone/structural.py", f"_profile_{label}_clone")
    uid = _load(source, "ids/uid.py", f"_profile_{label}_uid")
    fallback = _load(source, "ids/uid.py", f"_profile_{label}_fallback")
    if stdlib_only:
        uid._uuid7 = getattr(uuid, "uuid7", uid._stdlib_uuid7)
    cases = _workloads(clone, uid, fallback, stdlib_only)
    for case in cases:
        if not case.check(case.call()):
            msg = f"incorrect output: {label}/{case.name}"
            raise AssertionError(msg)
    signatures = {
        f"{prefix}.{name}": str(inspect.signature(getattr(module, name)))
        for prefix, module, names in (
            ("clone", clone, ("deep", "asdict", "copy", "deepcopy")),
            ("uid", uid, ("new", "new_hex", "parse", "derive")),
        )
        for name in names
    }
    profiles = _profile([clone, uid], cases, stdlib_only)
    return {
        "source": str(source),
        "cases": cases,
        "signatures": signatures,
        **profiles,
        "source_sha256": {
            relative: hashlib.sha256(
                (source / "unstd" / relative).read_bytes()
            ).hexdigest()
            for relative in ("clone/structural.py", "ids/uid.py")
        },
        "uuid_backend": f"{uid._uuid7.__module__}.{uid._uuid7.__name__}",
    }


def main() -> None:
    """Profile all implemented functions and compare validated workloads."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--stdlib-only", action="store_true")
    parser.add_argument("--profile-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 1 or args.repetitions < 1:
        parser.error("rounds and repetitions must be positive")
    bundles = {}
    if args.compare_source:
        bundles["before"] = _bundle(
            args.compare_source.resolve(), "before", args.stdlib_only
        )
    bundles["after"] = _bundle(args.source.resolve(), "after", args.stdlib_only)
    if len({str(bundle["signatures"]) for bundle in bundles.values()}) != 1:
        msg = "public signatures changed"
        raise AssertionError(msg)
    workload_names = [
        [case.name for case in bundle["cases"]] for bundle in bundles.values()
    ]
    if any(names != workload_names[0] for names in workload_names):
        msg = "comparison workloads differ"
        raise AssertionError(msg)
    timings: dict[str, Any] = {}
    if not args.profile_only:
        for index, name in enumerate(workload_names[0]):
            samples: dict[str, list[Any]] = {label: [] for label in bundles}
            for repetition in range(args.repetitions):
                order = list(bundles)[:: 1 if repetition % 2 == 0 else -1]
                for label in order:
                    measurement = measure(
                        bundles[label]["cases"][index].call, rounds=args.rounds
                    )
                    samples[label].append(dataclasses.asdict(measurement))
            medians = {
                label: statistics.median(sample["per_op_ns"] for sample in rows)
                for label, rows in samples.items()
            }
            timings[name] = {"samples": samples, "median_ns": medians}
            if "before" in medians:
                timings[name]["speedup"] = medians["before"] / medians["after"]
            if (index + 1) % 10 == 0:
                print(
                    f"Measured {index + 1}/{len(workload_names[0])} workloads",
                    file=sys.stderr,
                )
    backends: dict[str, str | None] = {}
    for name in ("uuid-utils", "pydantic", "msgspec"):
        try:
            backends[name] = version(name)
        except PackageNotFoundError:
            backends[name] = None
    output = {
        "python": sys.version,
        "platform": platform.platform(),
        "backends": backends,
        "stdlib_only": args.stdlib_only,
        "rounds": args.rounds,
        "repetitions": args.repetitions,
        "profiles": {
            label: {key: value for key, value in bundle.items() if key != "cases"}
            for label, bundle in bundles.items()
        },
        "timings": timings,
    }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)
    for label, bundle in bundles.items():
        covered = sum(bool(function["calls"]) for function in bundle["functions"])
        print(
            f"{label}: {covered}/{len(bundle['functions'])} runtime Python functions profiled; {len(bundle['cases'])} checked workloads",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
