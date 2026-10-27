"""Compare saved/current public Jaro-Winkler scoring without RapidFuzz dispatch.

Independent RapidFuzz scores validate fixtures outside timing. Both native-capable
and forced-portable wrappers run alternating, independently calibrated batches.
Candidate-only scaling rows never invent a timing for the saved implementation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import gc
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import tracemalloc
from typing import TYPE_CHECKING, Any


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bench.core.timing import _calibrate
from bench.profiles.text_affixes import _batch, _receipt
from bench.profiles.text_native_compare import _bundle


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Case:
    """A pairwise score or complete ranking of a tiny choice collection."""

    name: str
    a: str
    b: str
    choices: tuple[str, ...] = ()


def _cases() -> list[Case]:
    rng = random.Random(1729)
    diverse = "".join(chr(0x400 + i) for i in range(1000))
    return [
        Case("short-name", "MARTHA", "MARHTA"),
        Case("short-unrelated", "Jane", "John"),
        Case("repeated-3000", "a" * 3000, "a" * 2999 + "b"),
        Case("repeated-10000", "a" * 10000, "a" * 9999 + "b"),
        Case("disjoint-3000", "a" * 3000, "b" * 3000),
        Case(
            "seeded-varied-3000",
            "".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=3000)),
            "".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=3000)),
        ),
        Case("mixed-unicode-3000", "aé龍😀" * 750, "aé龍😀" * 749 + "aé龍x"),
        Case("diverse-unicode-1000", diverse, diverse[::-1]),
        Case("unbalanced-matches", "ab", "a" * 100000 + "c"),
        Case("unbalanced-matches-reversed", "a" * 100000 + "c", "ab"),
        Case("unbalanced-disjoint", "a", "b" * 100000),
        Case("unbalanced-disjoint-reversed", "b" * 100000, "a"),
        Case(
            "best-match-3000",
            "a" * 3000,
            "",
            ("b" * 3000, "a" * 2999 + "b", "a" * 2998 + "bb"),
        ),
    ]


def _call(wrapper: Any, case: Case) -> Callable[[], Any]:
    if case.choices:
        return partial(
            wrapper.best_match,
            case.a,
            case.choices,
            scorer=wrapper.jaro_winkler,
        )
    return partial(wrapper.jaro_winkler, case.a, case.b)


def _oracle(case: Case, similarity: Callable[[str, str], float]) -> Any:
    if case.choices:
        return max(
            ((b, similarity(case.a, b)) for b in case.choices), key=lambda x: x[1]
        )
    return similarity(case.a, case.b)


def _check(actual: Any, expected: Any, name: str, *, oracle: bool = False) -> None:
    if isinstance(expected, tuple):
        correct = actual[0] == expected[0]
        actual, expected = actual[1], expected[1]
    else:
        correct = True
    correct = correct and (
        math.isclose(actual, expected, rel_tol=0, abs_tol=2e-16)
        if oracle
        else actual == expected
    )
    if not correct:
        msg = f"incorrect score: {name}: {actual!r} != {expected!r}"
        raise AssertionError(msg)


def _route(wrapper: Any, case: Case) -> list[str]:
    native = wrapper._native_jaro
    events: list[str] = []

    def observed(a: str, b: str) -> Any:
        result = native(a, b)
        events.append(
            "native-declined; portable" if result is NotImplemented else "native"
        )
        return result

    wrapper._native_jaro = observed if native is not None else None
    try:
        _call(wrapper, case)()
    finally:
        wrapper._native_jaro = native
    return events or ["portable or equality/empty shortcut"]


def _summarize(samples: dict[str, list[dict[str, float]]]) -> dict[str, Any]:
    row: dict[str, Any] = {"samples": samples}
    for clock in ("wall", "cpu"):
        row[f"median_{clock}_ns"] = {
            arm: statistics.median(s[f"{clock}_ns"] for s in values)
            for arm, values in samples.items()
        }
    if "baseline" in samples:
        row["paired_speedup"] = statistics.median(
            b["wall_ns"] / a["wall_ns"]
            for b, a in zip(samples["baseline"], samples["candidate"], strict=True)
        )
    return row


def main() -> None:
    """Save checked timings and receipts before enforcing the improvement guard."""
    import rapidfuzz
    from rapidfuzz.distance import JaroWinkler

    parser = argparse.ArgumentParser(description=__doc__)
    for arm in ("baseline", "candidate"):
        parser.add_argument(f"--{arm}-source", type=Path, required=True)
        parser.add_argument(f"--{arm}-native", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error("at least three paired rounds are required")
    arms = ("baseline", "candidate")
    sources = {arm: getattr(args, f"{arm}_source").resolve() for arm in arms}
    binaries = {arm: getattr(args, f"{arm}_native").resolve() for arm in arms}
    receipts = {
        arm: {
            "wrapper": _receipt(source / "unstd/text/fuzz.py"),
            "native": _receipt(binaries[arm]),
        }
        for arm, source in sources.items()
    }
    cases = _cases()
    report: dict[str, Any] = {
        "utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "harness": [
            _receipt(Path(__file__)),
            _receipt(Path(__file__).with_name("text_affixes.py")),
            _receipt(Path(__file__).with_name("text_native_compare.py")),
            _receipt(Path(__file__).parents[1] / "core/timing.py"),
        ],
        "implementations": receipts,
        "fixture_sha256": hashlib.sha256(
            json.dumps(
                [vars(c) for c in cases], ensure_ascii=True, sort_keys=True
            ).encode()
        ).hexdigest(),
        "method": "isolated public base-install wrappers; independent per-arm calibration; alternating paired batch means; GC enabled; separate optional allocations",
        "oracle": "RapidFuzz JaroWinkler.similarity, absolute tolerance 2e-16; saved/current scores must agree exactly",
        "oracle_version": rapidfuzz.__version__,
        "limitation": "synthetic workloads on one host; excludes the unchanged RapidFuzz-present public binding; tracemalloc excludes native malloc",
        "rows": {},
        "candidate_only_scaling": {},
    }

    def save() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")

    for lane in ("native", "portable"):
        wrappers = {
            arm: _bundle(binaries[arm], sources[arm], f"jaro_{lane}_{arm}")["wrapper"]
            for arm in arms
        }
        if lane == "portable":
            for wrapper in wrappers.values():
                wrapper._native_jaro = None
        for case in cases:
            calls = {arm: _call(wrapper, case) for arm, wrapper in wrappers.items()}
            scores = {arm: call() for arm, call in calls.items()}
            expected = _oracle(case, JaroWinkler.similarity)
            for arm, score in scores.items():
                _check(score, expected, f"{lane}/{case.name}/{arm}", oracle=True)
            _check(scores["candidate"], scores["baseline"], case.name)
            routes = {arm: _route(wrapper, case) for arm, wrapper in wrappers.items()}
            counts = {arm: _calibrate(call)[0] for arm, call in calls.items()}
            samples: dict[str, list[dict[str, float]]] = {arm: [] for arm in arms}
            orders = []
            for index in range(args.rounds):
                order = arms if index % 2 == 0 else arms[::-1]
                orders.append(order)
                for arm in order:
                    samples[arm].append(_batch(calls[arm], counts[arm]))
            for arm, call in calls.items():
                _check(call(), scores[arm], f"post-timing/{case.name}/{arm}")
            peaks = {}
            # Tracing the old quadratic 10k loop is disproportionately costly.
            if len(case.a) <= 3000 and not case.choices:
                for arm, call in calls.items():
                    gc.collect()
                    tracemalloc.start()
                    try:
                        call()
                        peaks[arm] = tracemalloc.get_traced_memory()[1]
                    finally:
                        tracemalloc.stop()
            row = {
                "lengths": [len(case.a), len(case.b)],
                "choice_lengths": list(map(len, case.choices)),
                "scores": scores,
                "independent_expected": expected,
                "routes": routes,
                "iterations": counts,
                "order": orders,
                "peak_python_allocation_bytes": peaks or None,
                **_summarize(samples),
            }
            report["rows"][f"{lane}/{case.name}"] = row
            print(
                f"{lane}/{case.name}: {row['median_wall_ns']['baseline'] / 1e6:.6f}ms -> {row['median_wall_ns']['candidate'] / 1e6:.6f}ms ({row['paired_speedup']:.2f}x)",
                flush=True,
            )
            save()
        for length in (10000, 100000):
            case = Case(f"repeated-{length}", "a" * length, "a" * (length - 1) + "b")
            call = _call(wrappers["candidate"], case)
            expected = _oracle(case, JaroWinkler.similarity)
            _check(call(), expected, f"scaling/{lane}/{length}", oracle=True)
            count = _calibrate(call)[0]
            row = {
                "lengths": [length, length],
                "expected": expected,
                "route": _route(wrappers["candidate"], case),
                "iterations": count,
                **_summarize(
                    {"candidate": [_batch(call, count) for _ in range(args.rounds)]}
                ),
            }
            report["candidate_only_scaling"][f"{lane}/{length}"] = row
            save()
    for arm, source in sources.items():
        if receipts[arm] != {
            "wrapper": _receipt(source / "unstd/text/fuzz.py"),
            "native": _receipt(binaries[arm]),
        }:
            report["invalidated"] = f"implementation changed during timing: {arm}"
            save()
            raise RuntimeError(report["invalidated"])
    failures = [
        f"{lane}/repeated-10000 below 100x"
        for lane in ("native", "portable")
        if report["rows"][f"{lane}/repeated-10000"]["paired_speedup"] < 100
    ]
    report["improvement_guard"] = {
        "minimum_repeated_10000_speedup": 100,
        "failures": failures,
    }
    save()
    if failures:
        raise AssertionError("; ".join(failures))


if __name__ == "__main__":
    main()
