"""Output-checked profiles and paired timings for the four bundled C kernels.

Run ``python bench/native_profile.py --compare-source /tmp/before/src``.
The comparison directory must contain ``unstd``. Text measurements deliberately
select the base-install scorers, even when rapidfuzz is installed. Profiling is
separate from timing, and a missing or unexercised C kernel fails explicitly.
The comparison must use Python-only implementations of the measured functions.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import cProfile
import dataclasses
from dataclasses import dataclass
from functools import partial
import hashlib
import importlib
import importlib.machinery
import importlib.util
import inspect
import json
from pathlib import Path
import platform
import statistics
import sys
from typing import TYPE_CHECKING, Any


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


if TYPE_CHECKING or __package__:
    from bench.core.timing import measure
else:
    from bench.core.timing import measure

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType


_SOURCE_FILES = ("clone/structural.py", "text/fuzz.py")
_NATIVE_NAMES = ("unstd.clone._clone_native", "unstd.text._text_native")


@dataclass
class Workload:
    """One public call and its untimed compatibility check."""

    name: str
    call: Callable[[], Any]
    check: Callable[[Any], bool]


@dataclass
class Record:
    """Ordinary records exercise native reconstruction with a shared memo."""

    name: str
    values: list[int]


class Custom:
    """An observable user protocol that must retain Python fallback handling."""

    def __deepcopy__(self, memo: Any) -> dict[str, list[int]]:
        """Produce a fresh mutable marker through the standard copy protocol."""
        return {"custom": [7, 8]}


class StringSubclass(str):
    """A foreign string type exercises guarded text fallback selection."""


def _same(actual: Any, expected: Any) -> bool:
    """Compare recursively without losing exact types or dictionary order."""
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
            _same(value, old_value)
            for value, old_value in zip(actual, expected, strict=True)
        )
    if dataclasses.is_dataclass(expected) and not isinstance(expected, type):
        return _same(actual.__dict__, expected.__dict__)
    return bool(actual == expected)


def _isolated(actual: Any, original: Any) -> bool:
    """Reject reused mutable branches; immutable tuple identity is irrelevant."""
    if type(original) is dict:
        return actual is not original and all(
            _isolated(key, old_key) and _isolated(value, old_value)
            for (key, value), (old_key, old_value) in zip(
                actual.items(), original.items(), strict=True
            )
        )
    if type(original) in (list, tuple):
        return (type(original) is tuple or actual is not original) and all(
            _isolated(value, old_value)
            for value, old_value in zip(actual, original, strict=True)
        )
    if dataclasses.is_dataclass(original) and not isinstance(original, type):
        return actual is not original and _isolated(actual.__dict__, original.__dict__)
    return True


def _clone_check(actual: Any, *, expected: Any, original: Any) -> bool:
    return _same(actual, expected) and _isolated(actual, original)


def _load(source: Path, relative: str, name: str) -> Any:
    path = source / "unstd" / relative
    if not path.is_file():
        msg = f"missing comparison source: {path}"
        raise FileNotFoundError(msg)
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


def _base_text(fuzz: Any) -> dict[str, str]:
    """Select the real base-install wrappers without hiding their C dispatch."""
    selected = {
        name: f"{getattr(fuzz, name).__module__}.{getattr(fuzz, name).__name__}"
        for name in ("distance", "jaro_winkler")
    }
    fuzz._HAVE_RAPIDFUZZ = False
    fuzz.distance = fuzz._levenshtein
    fuzz.jaro_winkler = fuzz._jaro_winkler
    return selected


def _workloads(clone: Any, fuzz: Any) -> list[Workload]:
    cases: list[Workload] = []
    for name, value in (
        ("small", {"id": 7, "name": "café", "ok": True}),
        (
            "records200",
            [{"id": i, "values": [i, "café", (True, None)]} for i in range(200)],
        ),
        (
            "records500",
            [{"id": i, "name": "東京", "score": 1.5} for i in range(500)],
        ),
        ("mixed-custom-fallback", {"data": [1, 2], "custom": Custom()}),
        (
            "dataclass-records",
            [Record("row", [i, i + 1]) for i in range(200)],
        ),
        (
            "dataclass-wrapped",
            {"rows": [Record("row", [i, i + 1]) for i in range(200)]},
        ),
    ):
        expected = copy.deepcopy(value)
        cases.append(
            Workload(
                f"clone.deep/{name}",
                partial(clone.deep, value),
                partial(_clone_check, expected=expected, original=value),
            )
        )
    shared = [1, {"value": [2]}]
    aliased = [shared, shared]
    preserves_aliases = "memo" in inspect.signature(clone._walk).parameters
    cases.append(
        Workload(
            "clone.deep/shared-branch-semantics",
            partial(clone.deep, aliased),
            lambda result: (
                _same(result, aliased)
                and _isolated(result, aliased)
                and (result[0] is result[1]) == preserves_aliases
                and (result[0][1]["value"] is result[1][1]["value"])
                == preserves_aliases
            ),
        )
    )
    cycle: list[Any] = []
    cycle.append(cycle)
    cases.append(
        Workload(
            "clone.deep/cycle-fallback",
            partial(clone.deep, cycle),
            lambda result: (
                type(result) is list and result is not cycle and result[0] is result
            ),
        )
    )
    for size in (12, 64, 65, 256, 1024):
        a = ("café東京🦀αβ" * (size // 9 + 1))[:size]
        b = a[: size // 3] + "é🦀" + a[size // 3 + 2 : -1] + "Z"
        # The simple dynamic program is an independent oracle for both native
        # and packed-integer implementations; it stays outside measurement.
        expected_distance = fuzz._sequence_distance(a, b)
        cases.append(
            Workload(
                f"text.distance/unicode{size}",
                partial(fuzz.distance, a, b),
                partial(_same, expected=expected_distance),
            )
        )
    cases.append(
        Workload(
            "text.distance/sequence-fallback",
            partial(fuzz.distance, [[1], [2], [3]], [[1], [4]]),
            lambda result: type(result) is int and result == 2,
        )
    )
    cases.append(
        Workload(
            "text.distance/subclass-fallback",
            partial(fuzz.distance, StringSubclass("café"), StringSubclass("cafe")),
            lambda result: type(result) is int and result == 1,
        )
    )
    for name, a, b, expected_score in (
        ("short-name", "MARTHA", "MARHTA", 0.9611111111111111),
        (
            "unicode75",
            ("María東京🦀" * 10)[:75],
            ("María東京🦀" * 10)[:35] + "ß" + ("María東京🦀" * 10)[36:75],
            None,
        ),
        ("subclass-fallback", StringSubclass("Jon"), StringSubclass("John"), None),
    ):
        if expected_score is None:
            # Filled from an independent source bundle before comparison.
            expected_score = fuzz.jaro_winkler(a, b)
        cases.append(
            Workload(
                f"text.jaro_winkler/{name}",
                partial(fuzz.jaro_winkler, a, b),
                partial(_same, expected=expected_score),
            )
        )
    return cases


def _signatures(clone: Any, fuzz: Any) -> dict[str, str]:
    return {
        f"{prefix}.{name}": str(inspect.signature(getattr(module, name)))
        for prefix, module, names in (
            ("clone", clone, clone.__all__),
            ("text", fuzz, fuzz.__all__),
        )
        for name in names
    }


def _profile(cases: list[Workload], source: Path) -> dict[str, Any]:
    """Retain Python and C entries separately so C execution remains visible."""
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        for _ in range(3):
            for case in cases:
                case.call()
    finally:
        profiler.disable()
    python, native = [], []
    source_root = str(source.resolve()) + "/"
    for entry in profiler.getstats():
        code = entry.code
        row = {
            "calls": entry.callcount,
            "recursive_calls": entry.reccallcount,
            "self_ns": entry.inlinetime * 1e9,
            "cumulative_ns": entry.totaltime * 1e9,
        }
        if inspect.iscode(code):
            if (
                code.co_filename.startswith(source_root)
                or code is copy.deepcopy.__code__
            ):
                python.append(
                    {
                        "function": code.co_name,
                        "file": code.co_filename,
                        "line": code.co_firstlineno,
                        **row,
                    }
                )
        else:
            native.append({"function": code, **row})
    return {
        "python": sorted(python, key=lambda row: (row["file"], row["line"])),
        "c": sorted(native, key=lambda row: row["function"]),
    }


def _native_modules(source: Path) -> dict[str, Any]:
    modules = {}
    for name in _NATIVE_NAMES:
        try:
            module = importlib.import_module(name)
        except ImportError as error:
            msg = f"required native module is unavailable: {name}; build it first"
            raise RuntimeError(msg) from error
        if module.__file__ is None:
            msg = f"required native module has no extension path: {name}"
            raise AssertionError(msg)
        path = Path(module.__file__).resolve()
        expected = (source / Path(*name.split(".")[:-1])).resolve()
        if path.parent != expected or not any(
            str(path).endswith(suffix)
            for suffix in importlib.machinery.EXTENSION_SUFFIXES
        ):
            msg = f"native module must be an extension from {expected}: {path}"
            raise AssertionError(msg)
        modules[name] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for directory in (
            expected,
            source.parent / "native" / name.split(".")[1] / "src",
        ):
            for filename in ("adapter.c", name.rsplit(".", 1)[-1] + ".c"):
                c_source = directory / filename
                if c_source.is_file():
                    modules[name]["c_source_sha256"] = hashlib.sha256(
                        c_source.read_bytes()
                    ).hexdigest()
                    break
            if "c_source_sha256" in modules[name]:
                break
    return modules


def _assert_native_execution(profile: dict[str, Any]) -> dict[str, int]:
    calls = {
        row["function"]: row["calls"]
        for row in profile["c"]
        if any(name in row["function"] for name in _NATIVE_NAMES)
    }
    for module, function in (
        (_NATIVE_NAMES[0], "clone_graph"),
        (_NATIVE_NAMES[0], "clone_dataclass"),
        (_NATIVE_NAMES[1], "distance"),
        (_NATIVE_NAMES[1], "jaro"),
    ):
        if not any(
            f"{module}.{function}>" in key and count for key, count in calls.items()
        ):
            msg = f"native kernel did not execute: {module}.{function}"
            raise AssertionError(msg)
    return calls


@contextmanager
def _native_results(
    clone: ModuleType, fuzz: ModuleType
) -> Iterator[tuple[dict[str, list[bool]], dict[str, int]]]:
    """Observe actual native returns only during untimed route profiling."""
    outcomes: dict[str, list[bool]] = {}
    recursion_errors: dict[str, int] = {}
    saved = []

    def track(function: Callable[..., Any], name: str) -> Callable[..., Any]:
        def recorded(*args: Any) -> Any:
            try:
                result = function(*args)
            except RecursionError:
                recursion_errors[name] = recursion_errors.get(name, 0) + 1
                raise
            outcomes.setdefault(name, []).append(result is not NotImplemented)
            return result

        return recorded

    try:
        for module, attribute, name in (
            (clone, "_native_graph", "graph"),
            (clone, "_native_dataclass", "dataclass"),
            (fuzz, "_native_distance", "distance"),
            (fuzz, "_native_jaro", "jaro"),
        ):
            original = getattr(module, attribute, None)
            if original is not None:
                saved.append((module, attribute, original))
                setattr(module, attribute, track(original, name))
        yield outcomes, recursion_errors
    finally:
        for module, attribute, original in saved:
            setattr(module, attribute, original)


def _routes(
    cases: list[Workload], source: Path, clone: Any, fuzz: Any
) -> dict[str, Any]:
    """Prove per-workload native execution and the declared Python fallbacks."""
    with _native_results(clone, fuzz) as (outcomes, recursion_errors):
        return _checked_routes(cases, source, outcomes, recursion_errors)


def _checked_routes(
    cases: list[Workload],
    source: Path,
    outcomes: dict[str, list[bool]],
    recursion_errors: dict[str, int],
) -> dict[str, Any]:
    """A C call that refuses its input is not evidence of acceleration."""
    routes = {}
    for case in cases:
        outcomes.clear()
        recursion_errors.clear()
        profile = _profile([case], source)
        c_calls = {
            row["function"]: row["calls"]
            for row in profile["c"]
            if any(name in row["function"] for name in _NATIVE_NAMES)
        }
        python_calls = {row["function"]: row["calls"] for row in profile["python"]}
        completed = {name: all(results) for name, results in outcomes.items()}
        if case.name.startswith("clone.deep/"):
            fallback = "fallback" in case.name
            if case.name == "clone.deep/cycle-fallback":
                valid = (
                    any("_clone_native.clone_graph>" in name for name in c_calls)
                    and recursion_errors.get("graph") == 3
                    and not outcomes
                    and bool(python_calls.get("deepcopy"))
                    and not python_calls.get("_walk")
                )
            elif case.name.startswith("clone.deep/dataclass-"):
                valid = (
                    any("_clone_native.clone_dataclass>" in name for name in c_calls)
                    and completed.get("dataclass", False)
                    and outcomes.get("graph") == [False, False, False]
                    and bool(python_calls.get("_walk"))
                )
            elif fallback:
                valid = bool(python_calls.get("_walk")) and not any(
                    any(results) for results in outcomes.values()
                )
            else:
                valid = (
                    any("_clone_native.clone_graph>" in name for name in c_calls)
                    and completed.get("graph", False)
                    and not python_calls.get("_walk")
                )
        else:
            fallback = "fallback" in case.name
            if fallback:
                valid = not c_calls
                if case.name.startswith("text.distance/"):
                    valid = valid and bool(python_calls.get("_sequence_distance"))
            else:
                kernel = "distance" if "text.distance/" in case.name else "jaro"
                valid = any(
                    f"_text_native.{kernel}>" in name for name in c_calls
                ) and completed.get(kernel, False)
        if not valid:
            msg = f"unexpected native/fallback route: {case.name}"
            raise AssertionError(msg)
        routes[case.name] = {
            "expected": "python fallback" if fallback else "native kernel",
            "c_calls": c_calls,
            "native_returns": {
                name: list(results) for name, results in outcomes.items()
            },
            "native_recursion_errors": dict(recursion_errors),
            "python_calls": python_calls,
        }
    return routes


def _bundle(source: Path, label: str, *, require_native: bool) -> dict[str, Any]:
    clone = _load(source, _SOURCE_FILES[0], f"_native_profile_{label}_clone")
    fuzz = _load(source, _SOURCE_FILES[1], f"_native_profile_{label}_fuzz")
    installed_signatures = _signatures(clone, fuzz)
    selected = _base_text(fuzz)
    cases = _workloads(clone, fuzz)
    for case in cases:
        if not case.check(case.call()):
            msg = f"incorrect output: {label}/{case.name}"
            raise AssertionError(msg)
    native = _native_modules(source) if require_native else {}
    profile = _profile(cases, source)
    if not require_native and any(
        name in row["function"] for row in profile["c"] for name in _NATIVE_NAMES
    ):
        msg = "comparison source executes bundled kernels; use a Python-only baseline"
        raise AssertionError(msg)
    execution = _assert_native_execution(profile) if require_native else {}
    routes = _routes(cases, source, clone, fuzz) if require_native else {}
    if clone.copy is not copy.copy or clone.deepcopy is not copy.deepcopy:
        msg = "clone stdlib aliases changed"
        raise AssertionError(msg)
    return {
        "source": str(source),
        "source_sha256": {
            relative: hashlib.sha256(
                (source / "unstd" / relative).read_bytes()
            ).hexdigest()
            for relative in _SOURCE_FILES
        },
        "signatures": _signatures(clone, fuzz),
        "installed_signatures": installed_signatures,
        "installed_text_selection": selected,
        "measured_text_selection": "base-install wrappers (rapidfuzz disabled)",
        "native_modules": native,
        "native_execution": execution,
        "routes": routes,
        "profile": profile,
        "cases": cases,
    }


def _validate_comparison(bundles: dict[str, dict[str, Any]]) -> None:
    before, after = bundles["before"], bundles["after"]
    if before["signatures"] != after["signatures"]:
        msg = "public signatures changed"
        raise AssertionError(msg)
    if before.get("installed_signatures") != after.get("installed_signatures"):
        msg = "installed public signatures changed"
        raise AssertionError(msg)
    if before.get("installed_text_selection") != after.get("installed_text_selection"):
        msg = "installed text bindings changed"
        raise AssertionError(msg)
    if [case.name for case in before["cases"]] != [
        case.name for case in after["cases"]
    ]:
        msg = "comparison workloads differ"
        raise AssertionError(msg)
    for old, new in zip(before["cases"], after["cases"], strict=True):
        old_result, new_result = old.call(), new.call()
        # Historical clone wrappers intentionally expanded aliases. Compare
        # their declared alias contracts separately while retaining type/value
        # agreement; all other workloads still require cross-contract checks.
        if old.name == "clone.deep/shared-branch-semantics":
            valid = (
                old.check(old_result)
                and new.check(new_result)
                and _same(old_result, new_result)
            )
        else:
            valid = old.check(new_result) and new.check(old_result)
        if not valid:
            msg = f"comparison output mismatch: {old.name}"
            raise AssertionError(msg)


def _timings(
    bundles: dict[str, dict[str, Any]], *, rounds: int, repetitions: int
) -> dict[str, Any]:
    timings = {}
    for index, case in enumerate(bundles["after"]["cases"]):
        samples: dict[str, list[Any]] = {label: [] for label in bundles}
        for repetition in range(repetitions):
            order = list(bundles)[:: 1 if repetition % 2 == 0 else -1]
            for label in order:
                measurement = measure(
                    bundles[label]["cases"][index].call, rounds=rounds
                )
                samples[label].append(dataclasses.asdict(measurement))
        medians = {
            label: statistics.median(sample["per_op_ns"] for sample in rows)
            for label, rows in samples.items()
        }
        timings[case.name] = {
            "samples": samples,
            "median_ns": medians,
            "speedup": medians["before"] / medians["after"],
        }
    return timings


def main() -> None:
    """Check compatibility and native execution before paired timing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--profile-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.rounds < 1 or args.repetitions < 1:
        parser.error("rounds and repetitions must be positive")
    # Absolute optional native imports in the source modules must bind to this
    # selected current tree, while the baseline retains its saved Python code.
    sys.path.insert(0, str(args.source.resolve()))
    bundles = {
        "before": _bundle(
            args.compare_source.resolve(), "before", require_native=False
        ),
        "after": _bundle(args.source.resolve(), "after", require_native=True),
    }
    _validate_comparison(bundles)
    output = {
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "rounds": args.rounds,
        "repetitions": args.repetitions,
        "profiles": {
            label: {key: value for key, value in bundle.items() if key != "cases"}
            for label, bundle in bundles.items()
        },
        "timings": {}
        if args.profile_only
        else _timings(bundles, rounds=args.rounds, repetitions=args.repetitions),
    }
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
