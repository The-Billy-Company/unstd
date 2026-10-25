"""Opt-in keyed count batching for captured Billy distractor consumers.

The count phase compares the exact per-key constructor+randint expression with
stateless public ``ints(..., seeds=...)``. Reused and newly constructed batch
streams are labelled separately. Full consumer cases retain sorting, hashing and
function-spec construction. Opt-in values change; all unchanged-API timings and
same-seed compatibility evidence belong to the separate ``billy_rand`` driver.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from functools import partial
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, override


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.profiles import billy_rand
from bench.profiles import rand_backend as timing


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def _seeds(examples: list[Any]) -> list[int]:
    return [
        int.from_bytes(
            hashlib.blake2b(
                f"{ex.hidden_tool}|{ex.messages[0]['content']}".encode(), digest_size=8
            ).digest(),
            "big",
        )
        for ex in examples
    ]


def _original_phase(module: ModuleType, seeds: list[int]) -> list[int]:
    return [module.Generator(seed, backend="stdlib").randint(1, 4) for seed in seeds]


def _keyed_phase(module: ModuleType, seeds: list[int], stream: Any | None) -> Any:
    stream = stream if stream is not None else module.Generator(173, backend="stdlib")
    return stream.ints(len(seeds), 1, 4, endpoint=True, seeds=seeds)


def _list(value: Any) -> Any:
    return value.tolist() if hasattr(value, "tolist") else value


def _check_counts(value: Any, n: int) -> None:
    values = _list(value)
    if (
        not isinstance(values, list)
        or len(values) != n
        or any(type(item) is not int or not 1 <= item <= 4 for item in values)
    ):
        message = "keyed count phase violated integer shape or [1,4] bounds"
        raise AssertionError(message)
    if n >= 1000 and any(
        not n * 0.15 <= Counter(values)[item] <= n * 0.35 for item in range(1, 5)
    ):
        message = "keyed count histogram failed the broad deterministic smoke check"
        raise AssertionError(message)


def _measure(
    calls: dict[str, Callable[[], Any]],
    check: Callable[[Any], None],
    *,
    rounds: int,
    minimum_ns: int,
) -> dict[str, Any]:
    for call in calls.values():
        check(call())
    calibration = {}
    for label, call in calls.items():
        calibration[label], result = timing._batch(call, 1)
        check(result)
    operations = min(
        100_000,
        max(
            1,
            max(
                (minimum_ns + sample["wall_ns"] - 1) // max(1, sample["wall_ns"])
                for sample in calibration.values()
            ),
        ),
    )
    samples: dict[str, list[dict[str, int]]] = {label: [] for label in calls}
    orders = []
    for round_index in range(rounds):
        order = list(calls) if round_index % 2 == 0 else list(calls)[::-1]
        orders.append(order)
        for label in order:
            sample, result = timing._batch(calls[label], operations)
            check(result)
            samples[label].append(sample)
    ratios = {
        metric: statistics.median(
            old[metric] / new[metric]
            for old, new in zip(samples["original"], samples["keyed"], strict=True)
        )
        for metric in ("wall_ns", "thread_cpu_ns")
    }
    return {
        "samples": samples,
        "orders": orders,
        "calibration": calibration,
        "median_per_call_ns": {
            label: {
                metric: statistics.median(
                    sample[metric] / sample["operations"] for sample in rows
                )
                for metric in ("wall_ns", "thread_cpu_ns")
            }
            for label, rows in samples.items()
        },
        "median_matched_original_over_keyed": ratios,
    }


def _load_fixture(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = path.read_bytes()
    receipts: dict[str, Any] = json.loads(raw)
    trees = {}
    for name, item in receipts["consumers"].items():
        tree = ast.parse(item["source"], filename=item["source_path"])
        symbols = {
            node.name: hashlib.sha256(
                ast.dump(node, include_attributes=False).encode()
            ).hexdigest()
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        if symbols != {
            symbol: value["ast_sha256"] for symbol, value in item["symbols"].items()
        }:
            message = f"keyed consumer AST disagrees with receipt: {name}"
            raise ValueError(message)
        trees[name] = tree
    receipts["fixture_sha256"] = hashlib.sha256(raw).hexdigest()
    return trees, receipts


class _KeyedCount(ast.NodeTransformer):
    """Explicit prospective rewrite of only the per-seed RNG count expression."""

    @override
    def visit_Call(self, node: ast.Call) -> ast.AST:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "randint":
            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id="keyed_count", ctx=ast.Load()),
                    args=[ast.Name(id="seed", ctx=ast.Load())],
                    keywords=[],
                ),
                node,
            )
        return self.generic_visit(node)


def _namespace(
    module: ModuleType, trees: dict[str, Any], receipts: dict[str, Any], *, keyed: bool
) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "rand": module,
        "hashlib": hashlib,
        "_MAX_DISTRACTORS": 4,
        "DISCOVERY_VERBS": ("find_tool", "tool_args", "use_tool", "forge_tool"),
    }
    for name in ("spec", "engine", "emit"):
        tree = ast.parse(receipts["consumers"][name]["source"])
        if keyed:
            tree = _KeyedCount().visit(tree)
        exec(  # noqa: S102 — checked local source plus declared opt-in rewrite
            compile(
                ast.fix_missing_locations(tree),
                receipts["consumers"][name]["source_path"],
                "exec",
            ),
            namespace,
        )
    return namespace


def _full(
    namespace: dict[str, Any],
    examples: list[Any],
    catalog: dict[str, Any],
    seeds: list[int],
    stream: Any | None,
    *,
    misleading: bool,
) -> Any:
    if stream is not None:
        # Complete migration cost includes deriving Python seed inputs, result
        # allocation and mapping. Captured bodies still perform their own hashes.
        batch_seeds = _seeds(examples)
        counts = stream.ints(len(batch_seeds), 1, 4, endpoint=True, seeds=batch_seeds)
        keyed_counts = dict(zip(batch_seeds, _list(counts), strict=True))
        namespace["keyed_count"] = keyed_counts.__getitem__
    if misleading:
        return [namespace["_misleading_distractors"](ex, catalog) for ex in examples]
    return [
        namespace["_distractor_specs"](catalog[ex.hidden_tool], ex, catalog)
        for ex in examples
    ]


def _check_full(
    value: Any, n: int, catalog: dict[str, Any], examples: list[Any]
) -> None:
    if not isinstance(value, list) or len(value) != n:
        message = "full distractor consumer returned wrong batch count"
        raise AssertionError(message)
    for specs, example in zip(value, examples, strict=True):
        names = [item["function"]["name"] for item in specs]
        if (
            not 1 <= len(specs) <= 4
            or len(set(names)) != len(names)
            or not set(names) <= catalog.keys()
            or example.hidden_tool in names
        ):
            message = "full distractor consumer violated count or tool membership"
            raise AssertionError(message)
        hidden = catalog[example.hidden_tool]
        siblings = sorted(
            (tool for name, tool in catalog.items() if name != example.hidden_tool),
            key=lambda tool: (tool.capability_key != hidden.capability_key, tool.name),
        )
        if names != [tool.name for tool in siblings[: len(names)]]:
            message = "full consumer changed the preferred sibling ordering"
            raise AssertionError(message)


def main(argv: list[str] | None = None) -> None:
    """Save paired phase/full migration evidence and explicit target results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path, required=True)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "workloads/rand_keyed.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=timing._positive, default=7)
    parser.add_argument("--batch-ms", type=timing._positive, default=5)
    parser.add_argument("--min-speedup", type=billy_rand._positive_float, default=100)
    parser.add_argument("--require-native", action="store_true")
    args = parser.parse_args(argv)
    if args.rounds < 7:
        parser.error("keyed qualification requires at least seven matched pairs")
    modules, evidence = {}, {}
    for label, source in {
        "keyed": args.source,
        "original": args.compare_source,
    }.items():
        modules[label], evidence[label] = timing._load_source(source)
    timing._check_sources(evidence)
    keyed_helper = evidence["keyed"]["helpers"].get("_keyed_ints", {})
    if args.require_native and not keyed_helper.get("native_binding"):
        parser.error("current keyed helper did not bind native seeded_ints")
    trees, receipts = _load_fixture(args.fixtures)
    namespaces = {
        label: _namespace(module, trees, receipts, keyed=label == "keyed")
        for label, module in modules.items()
    }
    catalog = {
        f"tool_{index}": SimpleNamespace(
            name=f"tool_{index}",
            capability_key=f"cap_{index % 14}",
            description=f"Synthetic tool {index}",
            args_schema={"type": "object", "properties": {}},
        )
        for index in range(371)
    }
    workloads = []
    stream = modules["keyed"].Generator(173, backend="stdlib")
    initial_state = timing._state(stream)
    for size in (24, 371, 8000):
        examples = [
            SimpleNamespace(
                hidden_tool=f"tool_{index % 371}",
                messages=[
                    {"role": "user", "content": f"public synthetic request {index}"}
                ],
                capability_key=f"cap_{index % 14}",
            )
            for index in range(size)
        ]
        seeds = _seeds(examples)
        keyed_values = _list(_keyed_phase(modules["keyed"], seeds, stream))
        if keyed_values != _list(
            _keyed_phase(modules["keyed"], seeds, stream)
        ) or keyed_values[::-1] != _list(
            _keyed_phase(modules["keyed"], seeds[::-1], stream)
        ):
            message = "keyed batch values changed with repetition or key ordering"
            raise AssertionError(message)
        for fresh in (False, True):
            calls: dict[str, Callable[[], Any]] = {
                "original": partial(_original_phase, modules["original"], seeds),
                "keyed": partial(
                    _keyed_phase, modules["keyed"], seeds, None if fresh else stream
                ),
            }
            row = _measure(
                calls,
                partial(_check_counts, n=size),
                rounds=args.rounds,
                minimum_ns=args.batch_ms * 1_000_000,
            )
            before, after = _list(calls["original"]()), _list(calls["keyed"]())
            row.update(
                {
                    "name": f"count-phase-{size}-{'fresh' if fresh else 'reused'}-batch-generator",
                    "scope": "per-seed distractor count phase; input seed hashes prepared outside timing, all original per-key construction and output allocation included",
                    "size": size,
                    "batch_generator_construction_included": fresh,
                    "original_histogram": dict(Counter(before)),
                    "keyed_histogram": dict(Counter(after)),
                    "required_speedup": args.min_speedup,
                    "target_passed": row["median_matched_original_over_keyed"][
                        "wall_ns"
                    ]
                    >= args.min_speedup,
                    "same_seed_output_equality_required": False,
                }
            )
            workloads.append(row)
        # Full consumer cases use small and catalog-sized batches. The 8000 case
        # qualifies the count phase without duplicating 371-tool sorts 8000 times.
        if size <= 371:
            for misleading in (False, True):
                calls = {
                    label: partial(
                        _full,
                        namespaces[label],
                        examples,
                        catalog,
                        seeds,
                        stream if label == "keyed" else None,
                        misleading=misleading,
                    )
                    for label in modules
                }
                row = _measure(
                    calls,
                    partial(_check_full, n=size, catalog=catalog, examples=examples),
                    rounds=args.rounds,
                    minimum_ns=args.batch_ms * 1_000_000,
                )
                row.update(
                    {
                        "name": f"full-{'misleading' if misleading else 'engine'}-consumer-{size}",
                        "scope": "full captured synchronous consumer functions with explicit prospective batched count-expression rewrite; sorting, seed hashing, mapping and tool-spec allocations included",
                        "size": size,
                        "required_speedup": args.min_speedup,
                        "target_passed": row["median_matched_original_over_keyed"][
                            "wall_ns"
                        ]
                        >= args.min_speedup,
                        "same_seed_output_equality_required": False,
                    }
                )
                workloads.append(row)
    if timing._state(stream) != initial_state:
        message = "keyed opt-in calls changed the selected instance's random state"
        raise AssertionError(message)
    timing._check_sources(evidence)
    if (
        hashlib.sha256(args.fixtures.read_bytes()).hexdigest()
        != receipts["fixture_sha256"]
    ):
        message = "keyed fixture changed during measurement"
        raise RuntimeError(message)
    report = {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "numpy": getattr(sys.modules.get("numpy"), "__version__", None),
        },
        "sources": evidence,
        "consumer_receipts": receipts,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Opt-in count strategy migration; count-phase gains are reported separately from complete captured consumer costs. No application files changed. Input payloads are synthetic.",
        "semantic_change": "SplitMix64-v1 keyed integers change same-seed distractor counts relative to one newly constructed MT19937 generator per key. Output bounds, deterministic per-key values and count distributions are checked; exact historical values are not promised.",
        "instance_state_unchanged": True,
        "keyed_repetition_and_reordering_checks": True,
        "timing_harness_sha256": hashlib.sha256(
            Path(timing.__file__).read_bytes()
        ).hexdigest(),
        "rounds": args.rounds,
        "workloads": workloads,
        "all_count_phase_targets_passed": all(
            row["target_passed"]
            for row in workloads
            if row["name"].startswith("count-phase")
        ),
        "all_full_consumer_targets_passed": all(
            row["target_passed"] for row in workloads if row["name"].startswith("full-")
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in workloads:
        print(
            f"{row['name']}: {row['median_matched_original_over_keyed']['wall_ns']:.2f}x, {args.min_speedup:g}x passed={row['target_passed']}"
        )
    if (
        not report["all_count_phase_targets_passed"]
        or not report["all_full_consumer_targets_passed"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
