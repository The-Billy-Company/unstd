"""Paired steady-state container mutations, separately from existing batch APIs.

Run this file directly after saving the baseline package and its native binary::

    python bench/profiles/containers_mutations.py \
        --compare-source /tmp/unstd-containers-mutations/before/src \
        --source src --size 4096 --rounds 7 --output /tmp/mutations.json

Repeat with --size 128, 65536, and 1000000. Random ID replacement and rolling
expiration keep the population constant. Indexed states first perform a middle
lookup. Keyed records and custom comparisons are separate fallback controls.
Isolated cases report add, remove, discard, pop and scalar deletion separately. Slice cases repeat
the same prefix/suffix ``del state[start:stop]`` API before and after, retaining
value identities and distinguishing aligned, safe partial and underflow cuts.
Append cases repeat ``update`` with an existing tuple or list of increasing IDs,
at n/10 elements, retaining payload references and checking input identities.
The batch group exercises the existing update, slice-delete and difference APIs;
its algorithm comparisons are explicitly separate from before/current speedups.

Setup, independent whole-state checks, return checks and profiling remain outside
timing. The shared container timing engine disables cyclic GC only around the
clocks. One private bytecode cache is warmed for both sources before paired fresh
workers alternate order. Iterations are bounded by population to limit memory.
There is no throughput threshold or promised universal speedup.
"""

from __future__ import annotations

import argparse
import cProfile
from dataclasses import dataclass
from hashlib import sha256
import importlib.util
import inspect
import json
import operator
from pathlib import Path
import random
from statistics import median
import sys
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bench.profiles import containers as core


_ROOT = Path(__file__).resolve().parents[2]
if not TYPE_CHECKING:
    # Importing bench/__init__ eagerly loads other product packages before a
    # worker can select its source. Load this standalone timing file directly.
    _spec = importlib.util.spec_from_file_location(
        "_unstd_containers_mutation_timing", Path(__file__).with_name("containers.py")
    )
    if _spec is None or _spec.loader is None:
        message = "cannot load the standalone container timing harness"
        raise RuntimeError(message)
    core = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = core
    _spec.loader.exec_module(core)


_SEED = 1729
_METHODS = (
    "add",
    "remove",
    "discard",
    "pop",
    "_delete",
    "_expand",
    "_loc",
    "_pos",
    "__delitem__",
    "update",
    "_update",
)


@dataclass(frozen=True, order=True, slots=True)
class ComparableID:
    """A Python comparison control, deliberately distinct from exact integers."""

    value: int


@dataclass(slots=True)
class ExclusiveIDs:
    """An index whose deleted integer objects have no external fixture owners."""

    _list: Any
    retained: tuple[int, ...]

    def __len__(self) -> int:
        """The current population of the index."""
        return len(self._list)


def _slice_windows(size: int) -> list[tuple[str, int, int]]:
    """Prefix/suffix expiration near n/10, with truthful load-boundary variants."""
    removed = max(1, size // 10)
    windows = [
        ("prefix.fraction", 0, removed),
        ("suffix.fraction", size - removed, size),
    ]
    if size >= 32768:
        load = 1000  # The supported sortedcontainers 2.4.0 default.
        prefix = (removed // load) * load
        suffix = ((size - removed) // load) * load
        windows.extend(
            (
                ("prefix.aligned", 0, prefix),
                ("prefix.partial_kept_large", 0, prefix + load // 4),
                ("prefix.partial_underflow", 0, prefix + load * 3 // 4),
                ("suffix.aligned", suffix, size),
                ("suffix.partial_kept_large", suffix + load * 3 // 4, size),
                ("suffix.partial_underflow", suffix + load // 4, size),
            )
        )
    return windows


def _cases(types: Any, size: int, batch: int) -> list[core.Case]:
    """Fixed mutations with models independent of either compared backend."""
    count = min(size, batch)
    base = tuple(range(0, size * 2, 2))
    positions = random.Random(_SEED).sample(range(size), count)
    replacements = tuple((base[index], base[index] + 1) for index in positions)
    expired = base[:count]
    arrivals = tuple(range(size * 2, (size + count) * 2, 2))
    random_model = list(base)
    for index in positions:
        random_model[index] += 1
    rolling_model = [*base[count:], *arrivals]
    cases: list[core.Case] = []

    def setup_factory(factory: Callable[[], Any], indexed: bool) -> Callable[[], Any]:
        def setup() -> Any:
            state = factory()
            if indexed:
                state[len(state) // 2] if not isinstance(
                    state, types.SortedDict
                ) else state.peekitem(len(state) // 2)
            return state

        return setup

    def sequence_check(
        expected: Sequence[Any], returns: Any
    ) -> Callable[[Any, Any], None]:
        def check(state: Any, result: Any) -> None:
            core._equal(list(state), list(expected))
            core._equal(len(state), len(expected))
            core._equal(result, returns)

        return check

    def random_replace(state: Any) -> Any:
        added = removed = None
        for old, new in replacements:
            added = state.add(new)
            removed = state.remove(old)
        return added, removed

    def rolling(state: Any) -> Any:
        removed = []
        added = None
        for new in arrivals:
            added = state.add(new)
            removed.append(state.pop(0))
        return added, tuple(removed)

    for family, factory in (
        ("sorted_list", lambda: types.SortedList(base)),
        ("sorted_set", lambda: types.SortedSet(base)),
    ):
        for indexed in (False, True):
            suffix = "indexed" if indexed else "unindexed"
            setup = setup_factory(factory, indexed)
            cases.extend(
                (
                    core.Case(
                        f"steady.{family}.random_replace.{suffix}",
                        setup,
                        random_replace,
                        sequence_check(random_model, (None, None)),
                        count * 2,
                    ),
                    core.Case(
                        f"steady.{family}.rolling_expiration.{suffix}",
                        setup,
                        rolling,
                        sequence_check(rolling_model, (None, expired)),
                        count * 2,
                    ),
                )
            )

    pairs = tuple((value, value) for value in base)
    expected_dict = {value: value for value in random_model}

    def dict_replace(state: Any) -> Any:
        for old, new in replacements:
            state[new] = new
            del state[old]
        return None

    def dict_check(state: Any, result: Any) -> None:
        core._equal(dict(state), expected_dict)
        core._equal(list(state), random_model)
        core._equal(result, None)

    for indexed in (False, True):
        suffix = "indexed" if indexed else "unindexed"
        cases.append(
            core.Case(
                f"steady.sorted_dict.random_replace.{suffix}",
                setup_factory(lambda: types.SortedDict(pairs), indexed),
                dict_replace,
                dict_check,
                count * 2,
            )
        )

    # Controls stay bounded independently of the large exact-integer population.
    control_size = min(size, 4096)
    control_count = min(count, control_size)
    control_base = tuple(range(0, control_size * 2, 2))
    control_positions = random.Random(_SEED).sample(range(control_size), control_count)
    control_model = list(control_base)
    for index in control_positions:
        control_model[index] += 1
    for keyed in (False, True):
        wrap: Callable[[int], Any] = (
            (lambda value: (value, str(value))) if keyed else ComparableID
        )
        data = tuple(wrap(value) for value in control_base)
        changes = tuple(
            (data[index], wrap(control_base[index] + 1)) for index in control_positions
        )
        expected = [wrap(value) for value in control_model]
        key = operator.itemgetter(0) if keyed else None

        def control_setup(data: Any = data, key: Any = key) -> Any:
            return types.SortedList(data, key=key)

        def control_replace(state: Any, changes: Any = changes) -> Any:
            added = removed = None
            for old, new in changes:
                added = state.add(new)
                removed = state.remove(old)
            return added, removed

        name = "keyed_records" if keyed else "custom_comparisons"
        cases.append(
            core.Case(
                f"fallback.{name}.random_replace",
                control_setup,
                control_replace,
                sequence_check(expected, (None, None)),
                control_count * 2,
            )
        )

    inserted = tuple(old + 1 for old in expired)
    insert_model = sorted((*base, *inserted))
    random_inserted = tuple(new for _old, new in replacements)
    isolated_insert_model = sorted((*base, *random_inserted))
    selected_positions = set(positions)
    isolated_delete_model = [
        value for index, value in enumerate(base) if index not in selected_positions
    ]
    scalar_delete_indices = tuple(sorted(positions, reverse=True))

    def add_only(state: Any) -> Any:
        result = None
        for value in random_inserted:
            result = state.add(value)
        return result

    def remove_only(state: Any) -> Any:
        result = None
        for old, _new in replacements:
            result = state.remove(old)
        return result

    def discard_only(state: Any) -> Any:
        result = None
        for old, _new in replacements:
            result = state.discard(old)
        return result

    def pop_only(state: Any) -> Any:
        return tuple(state.pop(0) for _ in expired)

    def scalar_delete_only(state: Any) -> Any:
        for index in scalar_delete_indices:
            del state[index]
        return None

    for indexed in (False, True):
        suffix = "indexed" if indexed else "unindexed"
        setup = setup_factory(lambda: types.SortedList(base), indexed)
        cases.extend(
            (
                core.Case(
                    f"isolated.sorted_list.add.{suffix}",
                    setup,
                    add_only,
                    sequence_check(isolated_insert_model, None),
                    count,
                ),
                core.Case(
                    f"isolated.sorted_list.remove.{suffix}",
                    setup,
                    remove_only,
                    sequence_check(isolated_delete_model, None),
                    count,
                ),
                core.Case(
                    f"isolated.sorted_list.discard.{suffix}",
                    setup,
                    discard_only,
                    sequence_check(isolated_delete_model, None),
                    count,
                ),
                core.Case(
                    f"isolated.sorted_list.pop.{suffix}",
                    setup,
                    pop_only,
                    sequence_check(base[count:], expired),
                    count,
                ),
                core.Case(
                    f"isolated.sorted_list.delitem.{suffix}",
                    setup,
                    scalar_delete_only,
                    sequence_check(isolated_delete_model, None),
                    count,
                ),
            )
        )
        for name, first, last in _slice_windows(size):
            model = [*base[:first], *base[last:]]

            def expire_slice(state: Any, first: int = first, last: int = last) -> Any:
                del state[first:last]
                return None

            def check_slice(state: Any, result: Any, model: Any = model) -> None:
                core._equal(list(state), model)
                core._equal(result, None)
                if not all(
                    actual is expected
                    for actual, expected in zip(state, model, strict=True)
                ):
                    message = "slice deletion replaced a retained value's identity"
                    raise AssertionError(message)

            cases.append(
                core.Case(
                    f"slice.sorted_list.{name}.{suffix}",
                    setup,
                    expire_slice,
                    check_slice,
                    last - first,
                )
            )

    append_count = max(1, size // 10)
    appended = tuple(range(size * 2, (size + append_count) * 2, 2))
    append_model = [*base, *appended]
    for indexed in (False, True):
        suffix = "indexed" if indexed else "unindexed"
        setup = setup_factory(lambda: types.SortedList(base), indexed)
        for kind in ("tuple", "list"):
            payload = appended if kind == "tuple" else list(appended)

            def append_update(state: Any, payload: Any = payload) -> Any:
                return state.update(payload)

            def check_append(state: Any, result: Any, payload: Any = payload) -> None:
                core._equal(list(state), append_model)
                core._equal(result, None)
                core._equal(list(payload), list(appended))
                if not all(
                    actual is expected
                    for actual, expected in zip(state, append_model, strict=True)
                ):
                    message = "append update replaced a retained value's identity"
                    raise AssertionError(message)
                if not all(
                    actual is expected
                    for actual, expected in zip(payload, appended, strict=True)
                ):
                    message = "append update changed the input payload's identities"
                    raise AssertionError(message)

            cases.append(
                core.Case(
                    f"append.sorted_list.update_{kind}.{suffix}",
                    setup,
                    append_update,
                    check_append,
                    append_count,
                )
            )

    # Unlike the ordinary retained-ID fixtures, deleted IDs here are created by
    # this setup's range and have no owner outside the container. The origin
    # excludes CPython's cached small integers, including at small populations.
    origin = 1_000_000_000
    exclusive_count = max(1, size // 10)
    for prefix in (False, True):
        first, last = (0, exclusive_count) if prefix else (size - exclusive_count, size)
        exclusive_expected = (
            tuple(range(origin + last * 2, origin + size * 2, 2))
            if prefix
            else tuple(range(origin, origin + first * 2, 2))
        )
        for indexed in (False, True):
            suffix = "indexed" if indexed else "unindexed"

            def exclusive_setup(
                first: int = first, last: int = last, indexed: bool = indexed
            ) -> ExclusiveIDs:
                ordered = types.SortedList(range(origin, origin + size * 2, 2))
                complete = tuple(ordered)
                retained = complete[last:] if first == 0 else complete[:first]
                del complete
                if indexed:
                    ordered[len(ordered) // 2]
                return ExclusiveIDs(ordered, retained)

            def exclusive_delete(
                state: ExclusiveIDs, first: int = first, last: int = last
            ) -> Any:
                del state._list[first:last]
                return None

            def exclusive_check(
                state: ExclusiveIDs, result: Any, expected: Any = exclusive_expected
            ) -> None:
                core._equal(tuple(state._list), expected)
                core._equal(result, None)
                if not all(
                    actual is retained
                    for actual, retained in zip(
                        state._list, state.retained, strict=True
                    )
                ):
                    message = (
                        "exclusive-ID deletion replaced a retained value's identity"
                    )
                    raise AssertionError(message)

            direction = "prefix" if prefix else "suffix"
            cases.append(
                core.Case(
                    f"slice.sorted_list.{direction}.exclusive_ids.{suffix}",
                    exclusive_setup,
                    exclusive_delete,
                    exclusive_check,
                    exclusive_count,
                )
            )

    def repeated_add(state: Any) -> Any:
        result = None
        for value in inserted:
            result = state.add(value)
        return result

    def repeated_pop(state: Any) -> Any:
        return tuple(state.pop(0) for _ in expired)

    def slice_delete(state: Any) -> Any:
        del state[:count]
        return None

    def repeated_discard(state: Any) -> Any:
        result = None
        for value in expired:
            result = state.discard(value)
        return result

    def set_difference(state: Any) -> Any:
        return state.difference_update(expired) is state

    cases.extend(
        (
            core.Case(
                "batch.sorted_list.repeated_add",
                lambda: types.SortedList(base),
                repeated_add,
                sequence_check(insert_model, None),
                count,
            ),
            core.Case(
                "batch.sorted_list.update",
                lambda: types.SortedList(base),
                lambda state: state.update(inserted),
                sequence_check(insert_model, None),
                count,
            ),
            core.Case(
                "batch.sorted_list.repeated_pop",
                lambda: types.SortedList(base),
                repeated_pop,
                sequence_check(base[count:], expired),
                count,
            ),
            core.Case(
                "batch.sorted_list.delete_slice",
                lambda: types.SortedList(base),
                slice_delete,
                sequence_check(base[count:], None),
                count,
            ),
            core.Case(
                "batch.sorted_set.repeated_discard",
                lambda: types.SortedSet(base),
                repeated_discard,
                sequence_check(base[count:], None),
                count,
            ),
            core.Case(
                "batch.sorted_set.difference_update",
                lambda: types.SortedSet(base),
                set_difference,
                sequence_check(base[count:], True),
                count,
            ),
        )
    )
    return cases


def _dispatch(types: Any) -> dict[str, Any]:
    result = core._dispatch(types)
    result["mutation_descriptors"] = {
        name: {
            method: ".".join(
                (
                    getattr(getattr(cls, method), "__objclass__", cls).__module__,
                    getattr(getattr(cls, method), "__objclass__", cls).__qualname__,
                )
            )
            for method in _METHODS
            if inspect.ismethoddescriptor(getattr(cls, method, None))
        }
        for name, cls in (
            ("SortedList", types.SortedList),
            ("SortedSet", types.SortedSet),
            ("SortedDict", types.SortedDict),
        )
    }
    return result


def _route_proof(case: core.Case) -> dict[str, Any]:
    """One checked diagnostic profile; none of its times enter comparisons."""
    state = case.setup()
    ordered = getattr(state, "_list", state)
    native = sys.modules.get("unstd.containers._sorted_native")
    stats_api = getattr(native, "mutation_stats", None)
    configured = any(
        name in vars(type(ordered))
        for name in ("_unstd_sorted_native_config", "_unstd_native_config")
    )
    if stats_api is not None and configured:
        stats_api(type(ordered), reset=True)
    profiler = cProfile.Profile()
    result = profiler.runcall(case.run, state)
    stats = stats_api(type(ordered)) if stats_api is not None and configured else None
    case.check(state, result)
    calls = []
    for entry in profiler.getstats():
        code = entry.code
        if isinstance(code, str):
            if not any(f"'{name}'" in code for name in _METHODS):
                continue
            name = code
        else:
            if not any(
                part in code.co_filename
                for part in ("sortedcontainers", "unstd/containers")
            ):
                continue
            name = f"{code.co_filename}:{code.co_firstlineno}:{code.co_name}"
        calls.append(
            {
                "function": name,
                "calls": entry.callcount,
                "builtin": isinstance(code, str),
            }
        )
    return {
        "calls": sorted(calls, key=lambda row: str(row["function"])),
        "mutation_stats": stats,
    }


def _worker(args: argparse.Namespace) -> dict[str, Any]:
    sys.path.insert(0, str(args.source.resolve()))
    import unstd.containers as containers

    cases = _cases(containers, args.size, args.batch)
    if args.include:
        cases = [
            case for case in cases if any(part in case.name for part in args.include)
        ]
    if not cases:
        message = "no mutation cases matched --include"
        raise ValueError(message)
    iterations = min(args.iterations, max(1, 262144 // args.size))
    results = {
        case.name: core._sample(case, iterations, smoke=args.smoke) for case in cases
    }
    # Reuse the established runtime/dependency fingerprints without timing its cases.
    environment = core._worker(args.source.resolve(), 1, 1, 1, smoke=True)[
        "environment"
    ]
    return {
        "environment": environment,
        "provenance": core._provenance(args.source.resolve()),
        "dispatch": _dispatch(containers),
        "population": args.size,
        "events": min(args.batch, args.size),
        "fallback_population": min(args.size, 4096),
        "cases": results,
        "route_proof": {
            case.name: _route_proof(case)
            for case in cases
            if case.name.startswith(
                ("steady.", "isolated.", "slice.", "append.", "fallback.")
            )
        }
        if args.proof
        else {},
    }


def _command(
    args: argparse.Namespace, source: Path, *, proof: bool = False
) -> list[str]:
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
        str(1 if proof else args.iterations),
    ]
    for part in args.include or ():
        command.extend(("--include", part))
    if args.smoke or proof:
        command.append("--smoke")
    if proof:
        command.append("--proof")
    return command


def _compare(args: argparse.Namespace) -> dict[str, Any]:
    sources = {
        "before": args.compare_source.resolve(),
        "current": args.source.resolve(),
    }
    runs = []
    proofs = []
    with TemporaryDirectory(prefix="unstd-mutations-pyc-") as temporary:
        cache = Path(temporary)
        for label, source in sources.items():
            result = core._run(
                _command(args, source, proof=True), source, cache, warm=True
            )
            proofs.append({"label": label, **result})
        for round_ in range(args.rounds):
            labels = list(sources)
            if round_ % 2:
                labels.reverse()
            for label in labels:
                source = sources[label]
                result = core._run(_command(args, source), source, cache)
                runs.append({"round": round_, "label": label, **result})
    core._validate_runs([*proofs, *runs])
    for proof in proofs:
        for name, route in proof.get("route_proof", {}).items():
            stats = route.get("mutation_stats") or {}
            if name.startswith("fallback.") and (
                stats.get("delete_fast", 0) or stats.get("update_fast", 0)
            ):
                message = (
                    "fallback control unexpectedly selected a bulk mutation fast path"
                )
                raise RuntimeError(message)
    if args.require_native_mutations:
        current = next(run for run in runs if run["label"] == "current")["dispatch"]
        if not {
            "add",
            "remove",
            "discard",
            "pop",
            "_delete",
            "_expand",
            "__delitem__",
            "update",
            "_update",
        }.intersection(current["mutation_descriptors"]["SortedList"]):
            message = "current source did not select native mutation descriptors"
            raise RuntimeError(message)
        target_names = set(
            next(run for run in runs if run["label"] == "current")["cases"]
        )
        if not any(
            name in target_names
            and name.startswith(("slice.", "append."))
            and (
                (route.get("mutation_stats") or {}).get("delete_fast", 0) > 0
                or (route.get("mutation_stats") or {}).get("update_fast", 0) > 0
            )
            for proof in proofs
            if proof["label"] == "current"
            for name, route in proof.get("route_proof", {}).items()
        ):
            message = "current source did not prove an actual bulk mutation fast path"
            raise RuntimeError(message)
    comparisons: dict[str, Any] = {}
    algorithms: dict[str, Any] = {}
    if not args.smoke:
        for name in runs[0]["cases"]:
            comparisons[name] = {}
            for metric in ("wall_ns_per_operation", "cpu_ns_per_operation"):
                samples = {
                    label: [
                        run["cases"][name][metric]
                        for run in runs
                        if run["label"] == label
                    ]
                    for label in sources
                }
                comparisons[name][metric] = {
                    "samples": samples,
                    "before_over_current": median(samples["before"])
                    / median(samples["current"]),
                }
        for repeated, bulk in (
            ("batch.sorted_list.repeated_add", "batch.sorted_list.update"),
            ("batch.sorted_list.repeated_pop", "batch.sorted_list.delete_slice"),
            ("batch.sorted_set.repeated_discard", "batch.sorted_set.difference_update"),
        ):
            if repeated not in comparisons or bulk not in comparisons:
                continue
            algorithms[bulk] = {
                label: median(
                    [
                        run["cases"][repeated]["wall_ns_per_operation"]
                        for run in runs
                        if run["label"] == label
                    ]
                )
                / median(
                    [
                        run["cases"][bulk]["wall_ns_per_operation"]
                        for run in runs
                        if run["label"] == label
                    ]
                )
                for label in sources
            }
    report = {
        "schema": 1,
        "complete": True,
        "fixture_seed": _SEED,
        "measurement_units": "per-operation timing normalizes by element mutations; slice cases normalize by deleted elements; wall_ns divided by iterations gives complete API-call latency",
        "ownership_controls": "ordinary fixtures retain ID objects outside the index; exclusive_ids slice fixtures construct fresh range IDs above the small-integer cache and retain external references only to surviving values, including deleted-PyLong deallocation in timing",
        "parameters": {
            "size": args.size,
            "batch": args.batch,
            "iterations_requested": args.iterations,
            "rounds": args.rounds,
            "smoke": args.smoke,
            "include": args.include,
        },
        "harness_sha256": {
            path.name: sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), Path(core.__file__))
        },
        "timing_policy": "setup and whole-state/return oracles outside clocks; cyclic GC disabled only during timed operations and restored afterward; no explicit per-case collection; one private bytecode cache prewarmed for both sources with writes enabled, measured fresh workers read it with writes disabled",
        "route_proof_policy": "separate checked cProfile passes before timed rounds; per-class mutation_stats reset after setup and read after each case; baseline without API records null; fallback controls must have zero fast counters; profile times never enter throughput comparisons",
        "proof_runs": proofs,
        "runs": runs,
        "before_current_comparisons": comparisons,
        "existing_api_algorithm_comparisons": algorithms,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    return report


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--size", type=int, default=4096)
    parser.add_argument("--batch", type=int, default=2048)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--include", action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--require-native-mutations", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--proof", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if min(args.size, args.batch, args.iterations, args.rounds) < 1:
        parser.error("positive size, batch, iterations and rounds required")
    if not args.worker and args.compare_source is None:
        parser.error("--compare-source is required")
    if not args.worker and not args.smoke and args.rounds < 3:
        parser.error("timing requires at least three alternating rounds")
    for source in (args.source, args.compare_source):
        if (
            source is not None
            and not (source / "unstd/containers/__init__.py").is_file()
        ):
            parser.error("source paths must contain unstd/containers/__init__.py")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    """Print the isolated worker result or paired evidence report."""
    args = _arguments(argv)
    print(json.dumps(_worker(args) if args.worker else _compare(args), indent=2))


if __name__ == "__main__":
    main()
