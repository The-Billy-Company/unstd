"""Paired container operations and genuinely cold imports on Linux or macOS.

Run this file directly so each worker selects its source before product import::

    python bench/profiles/containers.py --compare-source /saved/before/src \
        --source src --rounds 7 --output /tmp/containers.json

Setup, reset, and independent result checks remain outside operation timings.
Operation timings include allocation and reference-count cleanup, with cyclic
garbage collection disabled outside the clocks and restored afterward.
Cold imports use minimal fresh interpreters, before this harness or its metadata
dependencies load. Both sources first populate one private bytecode cache;
measured fresh processes read that cache with writes disabled. Samples alternate
source order and retain wall and process CPU time. There is deliberately no
machine-independent speed threshold.
"""

from __future__ import annotations

import argparse
import bisect
from dataclasses import dataclass
import gc
from hashlib import sha256
from importlib.metadata import distribution
import inspect
import json
import os
from pathlib import Path
import platform
import random
from statistics import median
import subprocess
import sys
import sysconfig
from tempfile import TemporaryDirectory
import time
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


@dataclass(frozen=True)
class Case:
    """A repeatable operation whose state is prepared and checked separately."""

    name: str
    setup: Callable[[], Any]
    run: Callable[[Any], Any]
    check: Callable[[Any, Any], None]
    operations: int


def _equal(actual: Any, expected: Any) -> None:
    if actual != expected:
        message = f"container oracle mismatch: {actual!r} != {expected!r}"
        raise AssertionError(message)


def _cases(types: Any, size: int, batch: int) -> list[Case]:
    """Public calls with plain Python models rather than backend self-checks."""
    base = tuple(range(0, size * 2, 2))
    extra = tuple(range(1, batch * 2, 2))
    rng = random.Random(1729)
    random_extra = [2 * ((index * size) // batch) + 1 for index in range(batch)]
    rng.shuffle(random_extra)
    scrambled = list(base)
    rng.shuffle(scrambled)
    victims = base[:batch]
    probes = tuple((index * size * 2) // batch for index in range(batch))
    positions = tuple((index * size) // batch for index in range(batch))
    signed_positions = tuple(
        position if index % 2 else position - size
        for index, position in enumerate(positions)
    )
    slices = (
        slice(size // 5, size * 4 // 5),
        slice(None, None, 3),
        slice(None, None, -2),
    )
    pairs = tuple((key, str(key)) for key in base)
    additions = tuple((key, str(key)) for key in extra)
    random_additions = tuple((key, str(key)) for key in random_extra)
    mapping = dict(pairs)
    updated = mapping | dict(additions)
    random_updated = mapping | dict(random_additions)
    cases: list[Case] = []

    def sequence(expected: Sequence[int]) -> Callable[[Any, Any], None]:
        def check(_state: Any, result: Any) -> None:
            _equal(list(result), list(expected))

        return check

    def dictionary(expected: dict[int, str]) -> Callable[[Any, Any], None]:
        def check(_state: Any, result: Any) -> None:
            _equal(dict(result), expected)
            _equal(list(result), sorted(expected))

        return check

    def persistent(_state: Any, result: Any) -> None:
        _equal(dict(_state), mapping)
        _equal(dict(result), updated)

    for keyed in (False, True):
        key = (lambda value: -value) if keyed else None
        ordered = sorted(base, key=key)
        combined = sorted((*base, *extra), key=key)
        random_combined = sorted((*base, *random_extra), key=key)
        remaining = sorted(base[batch:], key=key)
        # SortedKeyList.bisect_left accepts values and applies its key itself.
        search_keys = [-value for value in ordered] if keyed else ordered
        ranks = tuple(
            bisect.bisect_left(search_keys, -probe if keyed else probe)
            for probe in probes
        )
        prefix = "sorted_list.keyed" if keyed else "sorted_list"

        def setup(key: Any = key) -> Any:
            return types.SortedList(base, key=key)

        def construct(_state: Any, key: Any = key) -> Any:
            return types.SortedList(scrambled, key=key)

        def setup_indexed(key: Any = key) -> Any:
            state = types.SortedList(base, key=key)
            state[len(state) // 2]
            return state

        def add(state: Any) -> Any:
            for value in extra:
                state.add(value)
            return state

        def remove(state: Any) -> Any:
            for value in victims:
                state.remove(value)
            return state

        def add_random(state: Any) -> Any:
            for value in random_extra:
                state.add(value)
            return state

        def update(state: Any) -> Any:
            state.update(random_extra)
            return state

        def rank(state: Any) -> Any:
            return tuple(state.bisect_left(value) for value in probes)

        def check_ranks(_state: Any, result: Any, expected: Any = ranks) -> None:
            _equal(result, expected)

        def getitem(state: Any) -> Any:
            return tuple(state[index] for index in signed_positions)

        def rank_values(state: Any) -> Any:
            return tuple(state.index(value) for value in victims)

        def read_slices(state: Any) -> Any:
            return tuple(state[part] for part in slices)

        range_start, range_stop = size // 4, max(size // 4 + 1, size * 3 // 4)

        def read_range(
            state: Any,
            low: int = ordered[range_start],
            high: int = ordered[range_stop - 1],
        ) -> Any:
            return list(state.irange(low, high))

        def exact(expected: Any) -> Callable[[Any, Any], None]:
            def check(_state: Any, result: Any) -> None:
                _equal(result, expected)

            return check

        expected_indices = {value: index for index, value in enumerate(ordered)}

        cases.extend(
            (
                Case(
                    f"{prefix}.construct",
                    lambda: None,
                    construct,
                    sequence(ordered),
                    size,
                ),
                Case(f"{prefix}.add", setup, add, sequence(combined), batch),
                Case(
                    f"{prefix}.add_random",
                    setup,
                    add_random,
                    sequence(random_combined),
                    batch,
                ),
                Case(
                    f"{prefix}.add_indexed",
                    setup_indexed,
                    add_random,
                    sequence(random_combined),
                    batch,
                ),
                Case(f"{prefix}.remove", setup, remove, sequence(remaining), batch),
                Case(f"{prefix}.bisect", setup, rank, check_ranks, batch),
                Case(
                    f"{prefix}.getitem",
                    setup_indexed,
                    getitem,
                    exact(tuple(ordered[index] for index in signed_positions)),
                    batch,
                ),
                Case(
                    f"{prefix}.index",
                    setup_indexed,
                    rank_values,
                    exact(tuple(expected_indices[value] for value in victims)),
                    batch,
                ),
                Case(
                    f"{prefix}.slices",
                    setup,
                    read_slices,
                    exact(tuple(ordered[part] for part in slices)),
                    len(slices),
                ),
                Case(
                    f"{prefix}.irange",
                    setup,
                    read_range,
                    sequence(ordered[range_start:range_stop]),
                    1,
                ),
                Case(
                    f"{prefix}.update", setup, update, sequence(random_combined), batch
                ),
            )
        )

    def dict_insert(state: Any) -> Any:
        for key, value in additions:
            state[key] = value
        return state

    def set_add(state: Any) -> Any:
        for value in extra:
            state.add(value)
        return state

    def dict_insert_random(state: Any) -> Any:
        for key, value in random_additions:
            state[key] = value
        return state

    def dict_update(state: Any) -> Any:
        state.update(random_additions)
        return state

    def set_add_random(state: Any) -> Any:
        for value in random_extra:
            state.add(value)
        return state

    def set_update(state: Any) -> Any:
        state.update(random_extra)
        return state

    def map_set(state: Any) -> Any:
        for key, value in additions:
            state = state.set(key, value)
        return state

    def map_mutate(state: Any) -> Any:
        mutation = state.mutate()
        for key, value in additions:
            mutation[key] = value
        return mutation.finish()

    cases.extend(
        (
            Case(
                "sorted_dict.construct",
                lambda: None,
                lambda _: types.SortedDict(pairs),
                dictionary(mapping),
                size,
            ),
            Case(
                "sorted_dict.insert",
                lambda: types.SortedDict(pairs),
                dict_insert,
                dictionary(updated),
                batch,
            ),
            Case(
                "sorted_dict.insert_random",
                lambda: types.SortedDict(pairs),
                dict_insert_random,
                dictionary(random_updated),
                batch,
            ),
            Case(
                "sorted_dict.update",
                lambda: types.SortedDict(pairs),
                dict_update,
                dictionary(random_updated),
                batch,
            ),
            Case(
                "sorted_set.construct",
                lambda: None,
                lambda _: types.SortedSet(base),
                sequence(base),
                size,
            ),
            Case(
                "sorted_set.add",
                lambda: types.SortedSet(base),
                set_add,
                sequence(sorted((*base, *extra))),
                batch,
            ),
            Case(
                "sorted_set.add_random",
                lambda: types.SortedSet(base),
                set_add_random,
                sequence(sorted((*base, *random_extra))),
                batch,
            ),
            Case(
                "sorted_set.update",
                lambda: types.SortedSet(base),
                set_update,
                sequence(sorted((*base, *random_extra))),
                batch,
            ),
            Case(
                "map.construct",
                lambda: None,
                lambda _: types.Map(pairs),
                lambda _, result: _equal(dict(result), mapping),
                size,
            ),
            Case("map.set", lambda: types.Map(pairs), map_set, persistent, batch),
            Case(
                "map.update",
                lambda: types.Map(pairs),
                lambda state: state.update(additions),
                persistent,
                batch,
            ),
            Case("map.mutate", lambda: types.Map(pairs), map_mutate, persistent, batch),
        )
    )
    return cases


def _sample(case: Case, iterations: int, *, smoke: bool) -> dict[str, Any]:
    """Time only public operations; validate every independently prepared state."""
    states = [case.setup() for _ in range(iterations)]
    results: list[Any] = [None] * iterations
    if smoke:
        for index, state in enumerate(states):
            results[index] = case.run(state)
        timing: dict[str, Any] = {}
    else:
        collector_enabled = gc.isenabled()
        gc.disable()
        try:
            cpu_start = time.process_time_ns()
            wall_start = time.perf_counter_ns()
            try:
                for index, state in enumerate(states):
                    results[index] = case.run(state)
            finally:
                wall = time.perf_counter_ns() - wall_start
                cpu = time.process_time_ns() - cpu_start
        finally:
            if collector_enabled:
                gc.enable()
            else:
                gc.disable()
        timing = {
            "wall_ns": wall,
            "process_cpu_ns": cpu,
            "wall_ns_per_operation": wall / (iterations * case.operations),
            "cpu_ns_per_operation": cpu / (iterations * case.operations),
        }
    for state, result in zip(states, results, strict=True):
        case.check(state, result)
    return {
        "iterations": iterations,
        "operations_per_iteration": case.operations,
        **timing,
    }


def _fingerprint(name: str) -> dict[str, Any]:
    installed = distribution(name)
    hashes = {
        str(path): sha256(
            Path(str(installed.locate_file(path))).read_bytes()
        ).hexdigest()
        for path in sorted(installed.files or (), key=str)
        if path.suffix in {".py", ".so", ".pyd", ".dylib"}
    }
    return {"version": installed.version, "code_sha256": hashes}


def _provenance(source: Path) -> dict[str, Any]:
    import unstd.containers as containers

    imported = Path(containers.__file__).resolve()
    expected = source / "unstd/containers/__init__.py"
    if imported != expected:
        message = f"requested {expected}, imported {imported}"
        raise RuntimeError(message)
    return {
        "source": str(source),
        "imported": str(imported),
        "source_sha256": {
            str(path.relative_to(source)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(imported.parent.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".pyi", ".so", ".pyd"}
        }
        | {
            "unstd/__init__.py": sha256(
                (source / "unstd/__init__.py").read_bytes()
            ).hexdigest()
        },
    }


def _dispatch(types: Any) -> dict[str, Any]:
    """Describe selected classes and native descriptors without patching providers."""
    classes = {
        "SortedList": type(types.SortedList()),
        "SortedKeyList": type(types.SortedList(key=lambda value: -value)),
        "SortedDict": type(types.SortedDict()),
        "SortedSet": type(types.SortedSet()),
        "Map": type(types.Map()),
    }
    native = sys.modules.get("unstd.containers._sorted_native")
    native_path = getattr(native, "__file__", None)
    if native is not None and (
        native_path is None
        or Path(native_path).resolve().parent != Path(types.__file__).resolve().parent
    ):
        message = "native sorted extension resolved outside the requested source"
        raise RuntimeError(message)
    sorted_module = sys.modules.get("unstd.containers.sorted")
    methods = (
        "add",
        "bisect_left",
        "bisect_right",
        "_loc",
        "_pos",
        "_expand",
        "__getitem__",
        "update",
    )
    return {
        "native_module": native_path,
        "native_kernel_version": getattr(native, "KERNEL_VERSION", None),
        "have_native_sorted": getattr(sorted_module, "_HAVE_NATIVE", False),
        "classes": {
            name: {
                "module": cls.__module__,
                "qualname": cls.__qualname__,
                "native_configured": any(
                    attribute in vars(cls)
                    for attribute in (
                        "_unstd_native_config",
                        "_unstd_sorted_native_config",
                    )
                ),
                "native_configuration_attribute": next(
                    (
                        attribute
                        for attribute in (
                            "_unstd_native_config",
                            "_unstd_sorted_native_config",
                        )
                        if attribute in vars(cls)
                    ),
                    None,
                ),
                "native_descriptors": {
                    method: ".".join(
                        (
                            getattr(
                                getattr(cls, method), "__objclass__", cls
                            ).__module__,
                            getattr(
                                getattr(cls, method), "__objclass__", cls
                            ).__qualname__,
                        )
                    )
                    for method in methods
                    if inspect.ismethoddescriptor(getattr(cls, method, None))
                },
            }
            for name, cls in classes.items()
        },
    }


def _worker(
    source: Path, size: int, batch: int, iterations: int, *, smoke: bool
) -> dict[str, Any]:
    sys.path.insert(0, str(source))
    import unstd.containers as containers

    provenance = _provenance(source)
    cases = _cases(containers, size, batch)
    # Warm each operation and check its oracle before collecting samples.
    for case in cases:
        _sample(case, 1, smoke=True)
    return {
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "executable_sha256": sha256(
                Path(sys.executable).resolve().read_bytes()
            ).hexdigest(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "compiler_configuration": {
                name: sysconfig.get_config_var(name)
                for name in ("CC", "CFLAGS", "SOABI")
            },
            "backends": {
                name: _fingerprint(name) for name in ("sortedcontainers", "immutables")
            },
        },
        "provenance": provenance,
        "dispatch": _dispatch(containers),
        "cases": {case.name: _sample(case, iterations, smoke=smoke) for case in cases},
    }


_IMPORTS = {
    "package": "import unstd.containers",
    "map": "from unstd.containers import Map; Map()",
    "sorted_list": "from unstd.containers import SortedList; SortedList()",
    "all": "from unstd.containers import Map, SortedDict, SortedList, SortedSet; Map(); SortedDict(); SortedList(); SortedSet()",
}

# Do not import json, argparse, typing, or this harness before the clock starts.
_COLD_SCRIPT = """
import sys, time
sys.path.insert(0, sys.argv[1])
cpu = time.process_time_ns()
wall = time.perf_counter_ns()
exec(sys.argv[2])
wall = time.perf_counter_ns() - wall
cpu = time.process_time_ns() - cpu
import json
print(json.dumps({"wall_ns": wall, "process_cpu_ns": cpu,
    "imported": sys.modules["unstd.containers"].__file__,
    "backends_loaded": {name: name in sys.modules for name in ("sortedcontainers", "immutables")}}))
"""


def _run(
    command: list[str], source: Path, cache: Path, *, warm: bool = False
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source)
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONPYCACHEPREFIX"] = str(cache)
    if warm:
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
    else:
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Fixed interpreter/script/flags and explicit paths, without a shell.
    result = subprocess.run(  # noqa: S603
        command,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def _check_import_source(result: dict[str, Any], source: Path) -> None:
    if Path(result["imported"]).resolve() != source / "unstd/containers/__init__.py":
        message = "cold import resolved a different source"
        raise RuntimeError(message)


def _compare(args: argparse.Namespace) -> dict[str, Any]:
    sources = {"current": args.source.resolve()}
    if args.compare_source is not None:
        sources = {"before": args.compare_source.resolve(), **sources}
    # Pre-existing repository caches and saved sources have different histories.
    # Warm both dependency closures into the same clean cache before timing.
    with TemporaryDirectory(prefix="unstd-containers-pyc-") as cache:
        return _collect(args, sources, Path(cache))


def _collect(
    args: argparse.Namespace, sources: dict[str, Path], cache: Path
) -> dict[str, Any]:
    warm_imports = []
    if not args.smoke:
        for label, source in sources.items():
            result = _run(
                [sys.executable, "-c", _COLD_SCRIPT, str(source), _IMPORTS["all"]],
                source,
                cache,
                warm=True,
            )
            _check_import_source(result, source)
            warm_imports.append(
                {
                    "label": label,
                    "imported": result["imported"],
                    "backends_loaded": result["backends_loaded"],
                }
            )
    runs: list[dict[str, Any]] = []
    cold: list[dict[str, Any]] = []
    for round_ in range(args.rounds):
        labels = list(sources)
        if round_ % 2:
            labels.reverse()
        for label in labels:
            source = sources[label]
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker",
                "--source",
                str(source),
                "--size",
                str(args.size),
                "--batch",
                str(args.batch),
                "--iterations",
                str(args.iterations),
            ]
            if args.smoke:
                command.append("--smoke")
            result = _run(command, source, cache)
            runs.append({"round": round_, "label": label, **result})
        if not args.smoke:
            for name, statement in _IMPORTS.items():
                for label in labels:
                    source = sources[label]
                    result = _run(
                        [sys.executable, "-c", _COLD_SCRIPT, str(source), statement],
                        source,
                        cache,
                    )
                    _check_import_source(result, source)
                    cold.append(
                        {"round": round_, "label": label, "case": name, **result}
                    )
    _validate_runs(runs)
    if getattr(args, "require_native", False):
        for run in runs:
            if run["label"] != "current":
                continue
            dispatch = run.get("dispatch", {})
            selected = dispatch.get("classes", {}).get("SortedList", {})
            if (
                not dispatch.get("native_module")
                or not selected.get("native_configured")
                or not {"add", "_loc", "_pos"}.intersection(
                    selected.get("native_descriptors", {})
                )
            ):
                message = "current source did not select native sorted operations"
                raise RuntimeError(message)
    comparisons: dict[str, Any] = {}
    if "before" in sources and not args.smoke:
        for name in runs[0]["cases"]:
            row = {}
            for metric in ("wall_ns_per_operation", "cpu_ns_per_operation"):
                samples = {
                    label: [
                        run["cases"][name][metric]
                        for run in runs
                        if run["label"] == label
                    ]
                    for label in sources
                }
                row[metric] = {
                    "samples": samples,
                    "before_over_current": median(samples["before"])
                    / median(samples["current"]),
                }
            comparisons[name] = row
        for name in _IMPORTS:
            row = {}
            for metric in ("wall_ns", "process_cpu_ns"):
                samples = {
                    label: [
                        run[metric]
                        for run in cold
                        if run["label"] == label and run["case"] == name
                    ]
                    for label in sources
                }
                row[metric] = {
                    "samples": samples,
                    "before_over_current": median(samples["before"])
                    / median(samples["current"]),
                }
            comparisons[f"import.{name}"] = row
    report = {
        "schema": 1,
        "complete": True,
        "fixture_seed": 1729,
        "bytecode_policy": "one private pycache prefix per comparison; both sources and dependency closures prewarmed with writes enabled; measured fresh interpreters read warm bytecode with writes disabled",
        "gc_policy": {
            "operations": "cyclic collector disabled outside timing clocks; original state restored after clocks even on failure; allocation and reference-count cleanup retained; setup and oracles outside timing; no per-case collection",
            "cold_imports": "fresh interpreter default cyclic collector state",
            "smoke": "collector state untouched",
        },
        "warm_imports": warm_imports,
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "parameters": {
            name: getattr(args, name)
            for name in ("rounds", "size", "batch", "iterations", "smoke")
        },
        "runs": runs,
        "cold_imports": cold,
        "comparisons": comparisons,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    return report


def _validate_runs(runs: list[dict[str, Any]]) -> None:
    """Reject source or dependency drift rather than comparing unlike workers."""
    environments = [run["environment"] for run in runs]
    if any(environment != environments[0] for environment in environments[1:]):
        message = "worker environments differ"
        raise RuntimeError(message)
    for label in {run["label"] for run in runs}:
        selected = [run for run in runs if run["label"] == label]
        if any(run["provenance"] != selected[0]["provenance"] for run in selected[1:]):
            message = f"source changed between {label} samples"
            raise RuntimeError(message)
        if any(
            run.get("dispatch") != selected[0].get("dispatch") for run in selected[1:]
        ):
            message = f"selected dispatch changed between {label} samples"
            raise RuntimeError(message)
    if any(set(run["cases"]) != set(runs[0]["cases"]) for run in runs[1:]):
        message = "worker case inventories differ"
        raise RuntimeError(message)


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--size", type=int, default=4096)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--iterations", type=int, default=32)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--require-native",
        action="store_true",
        help="require the current source to select native sorted operations",
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if (
        min(args.rounds, args.size, args.batch, args.iterations) < 1
        or args.batch > args.size
    ):
        parser.error("positive rounds/size/batch/iterations and batch <= size required")
    if not args.smoke and not args.worker and args.rounds < 3:
        parser.error("timed comparisons require at least three alternating rounds")
    if not (args.source / "unstd/containers/__init__.py").is_file():
        parser.error("--source must contain unstd/containers/__init__.py")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    """Print a JSON evidence report or a single isolated worker sample."""
    args = _arguments(argv)
    result = (
        _worker(
            args.source.resolve(),
            args.size,
            args.batch,
            args.iterations,
            smoke=args.smoke,
        )
        if args.worker
        else _compare(args)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
