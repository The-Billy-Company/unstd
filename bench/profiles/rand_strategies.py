"""Prospective explicit sampling migration, separate from exact backend timings.

Compare captured Consumer full-shuffle-and-slice to the already available public
``Generator.sample(population, cap)`` API. The proposal changes selected values,
output order and RNG state for the same seed. No application call site is edited.
Construction and complete result allocation are included in every measured call.
"""

from __future__ import annotations

import argparse
import ast
from functools import partial
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
from typing import TYPE_CHECKING, Any


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.profiles import consumer_rand
from bench.profiles import rand_backend as timing


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def _original(
    module: ModuleType, function: Callable[..., Any], population: list[Any], cap: int
) -> list[Any]:
    return function(population, cap, module.Generator(timing.SEED, backend="stdlib"))  # type: ignore[no-any-return]


def _proposed(module: ModuleType, population: list[Any], cap: int) -> list[Any]:
    return module.Generator(timing.SEED, backend="stdlib").sample(population, cap)  # type: ignore[no-any-return]


def _check(result: Any, population: list[Any], cap: int) -> None:
    if not isinstance(result, list) or len(result) != cap:
        message = "strategy returned the wrong container or count"
        raise AssertionError(message)
    identities = {id(item) for item in result}
    if len(identities) != cap or not identities <= {id(item) for item in population}:
        message = "strategy violated membership or sampling without replacement"
        raise AssertionError(message)


def _measure(
    calls: dict[str, Callable[[], Any]],
    population: list[Any],
    cap: int,
    *,
    rounds: int,
    minimum_ns: int,
) -> dict[str, Any]:
    for call in calls.values():
        _check(call(), population, cap)
    calibration = {}
    for label, call in calls.items():
        calibration[label], result = timing._batch(call, 1)
        _check(result, population, cap)
    operations = max(
        1,
        max(
            (minimum_ns + sample["wall_ns"] - 1) // max(1, sample["wall_ns"])
            for sample in calibration.values()
        ),
    )
    samples: dict[str, list[dict[str, int]]] = {label: [] for label in calls}
    orders = []
    for round_index in range(rounds):
        order = list(calls) if round_index % 2 == 0 else list(calls)[::-1]
        orders.append(order)
        for label in order:
            sample, result = timing._batch(calls[label], operations)
            _check(result, population, cap)
            samples[label].append(sample)
    ratios = [
        old["wall_ns"] / new["wall_ns"]
        for old, new in zip(samples["original"], samples["proposed"], strict=True)
    ]
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
        "median_matched_original_over_proposed": statistics.median(ratios),
    }


def main(argv: list[str] | None = None) -> None:
    """Write a labelled prospective migration report, retaining unmet targets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path, required=True)
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "workloads/rand_strategies.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=timing._positive, default=7)
    parser.add_argument("--batch-ms", type=timing._positive, default=5)
    parser.add_argument(
        "--min-speedup", type=consumer_rand._positive_float, default=100
    )
    args = parser.parse_args(argv)
    if args.rounds < 3:
        parser.error("paired comparisons require at least three rounds")
    modules, evidence = {}, {}
    for label, source in {
        "proposed": args.source,
        "original": args.compare_source,
    }.items():
        modules[label], evidence[label] = timing._load_source(source)
    timing._check_sources(evidence)
    fixture_bytes = args.fixtures.read_bytes()
    receipts = json.loads(fixture_bytes)
    consumer = receipts["consumer"]
    tree = ast.parse(consumer["source"], filename=consumer["source_path"])
    symbol = tree.body[0]
    actual = hashlib.sha256(
        ast.dump(symbol, include_attributes=False).encode()
    ).hexdigest()
    if actual != consumer["symbols"]["_trim"]["ast_sha256"]:
        parser.error("captured trim AST disagrees with receipt")
    namespace: dict[str, Any] = {}
    exec(compile(tree, consumer["source_path"], "exec"), namespace)  # noqa: S102 — checked captured local source
    workloads = []
    for size_receipt in receipts["population_size_receipts"]:
        size = size_receipt["size"]
        population = [object() for _ in range(size)]
        for fraction in (0.01, 0.10, 0.50):
            cap = max(1, round(size * fraction))
            calls: dict[str, Callable[[], Any]] = {
                "original": partial(
                    _original, modules["original"], namespace["_trim"], population, cap
                ),
                "proposed": partial(_proposed, modules["proposed"], population, cap),
            }
            row = _measure(
                calls,
                population,
                cap,
                rounds=args.rounds,
                minimum_ns=args.batch_ms * 1_000_000,
            )
            row.update(
                {
                    "name": f"prospective-sample-population-{size}-cap-{cap}",
                    "population": size,
                    "cap": cap,
                    "fraction": fraction,
                    "size_receipt": size_receipt,
                    "required_speedup": args.min_speedup,
                    "target_passed": row["median_matched_original_over_proposed"]
                    >= args.min_speedup,
                }
            )
            workloads.append(row)
    timing._check_sources(evidence)
    if args.fixtures.read_bytes() != fixture_bytes:
        message = "strategy receipts changed during measurement"
        raise RuntimeError(message)
    report = {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "sources": evidence,
        "receipts": receipts,
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "proposal": "Replace full _trim shuffle-and-slice with Generator(seed, backend='stdlib').sample(population, cap); this API already existed before the backend work.",
        "semantic_change": "Opt-in migration changes same-seed selected values, output order and engine state. Only count, membership and unique identities are required to match. No app files were modified.",
        "method": "alternating paired complete calls including Generator construction and result allocation; fixtures and output checks outside timing; raw wall and thread CPU samples",
        "workloads": workloads,
        "all_targets_passed": all(row["target_passed"] for row in workloads),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in workloads:
        print(
            f"{row['name']}: {row['median_matched_original_over_proposed']:.3f}x, target {row['required_speedup']:g}x passed={row['target_passed']}"
        )
    if not report["all_targets_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
