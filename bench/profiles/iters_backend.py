"""Output-checked, adjacent paired timings for the pure-stdlib iterator backend.

Run ``python bench/profiles/iters_backend.py --compare-source saved/src
--source src --rounds 7 --output /tmp/iters.json``. Each tree's four modules
load under private aliases; package imports cannot select the other revision.
A synthetic bounded-selection workload compares an eager and a lazy recipe.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import partial
import gc
from hashlib import sha256
import importlib.util
from itertools import batched, pairwise, zip_longest
import json
from pathlib import Path
import platform
from statistics import median
import sys
import sysconfig
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from collections.abc import Callable


_MODULES = ("consecutive", "distinct", "reshape", "select")
_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Case:
    """One complete operation with a separately constructed expected result."""

    name: str
    run: Callable[[], Any]
    expected: Any
    note: str

    def check(self) -> None:
        """Reject incorrect values and output shapes outside the timed region."""
        actual = self.run()
        if type(actual) is not type(self.expected) or actual != self.expected:
            message = f"{self.name}: result differs from independent iterator oracle"
            raise AssertionError(message)


def _source_hashes(source: Path) -> dict[str, str]:
    package = source / "unstd" / "iters"
    if any(not (package / f"{name}.py").is_file() for name in _MODULES):
        message = f"source must contain all four unstd/iters modules: {source}"
        raise ValueError(message)
    return {
        str(path.relative_to(source)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(package.rglob("*.py"))
    }


def _load(source: Path, label: str) -> SimpleNamespace:
    _source_hashes(source)
    exports: dict[str, Any] = {"batched": batched, "pairwise": pairwise}
    for name in _MODULES:
        path = source / "unstd" / "iters" / f"{name}.py"
        alias = f"_iters_profile_{label}_{name}"
        spec = importlib.util.spec_from_file_location(alias, path)
        if spec is None or spec.loader is None:
            message = f"cannot load selected iterator source: {path}"
            raise ImportError(message)
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        # Execute the selected bytes even if a timestamp-valid stale .pyc exists.
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)  # noqa: S102
        exports.update({key: getattr(module, key) for key in module.__all__})
    return SimpleNamespace(**exports)


def _round_robin(groups: tuple[tuple[Any, ...], ...]) -> list[Any]:
    return [
        group[index]
        for index in range(max(map(len, groups), default=0))
        for group in groups
        if index < len(group)
    ]


def _cases(api: SimpleNamespace, *, eager_prefix: bool = False) -> list[Case]:
    values, bulk = tuple(range(64)), tuple(range(8192))
    duplicates = tuple(index // 4 for index in range(64))
    chunks = [values[start : start + 8] for start in range(0, 64, 8)]
    windows = [values[start : start + 4] for start in range(61)]

    def partition() -> tuple[list[int], list[int]]:
        left, right = api.partition(values, lambda value: value % 2)
        return list(left), list(right)

    cases = [
        Case(
            "batched/64", lambda: list(api.batched(values, 8)), chunks, "stdlib control"
        ),
        Case(
            "chunked/64", lambda: list(api.chunked(values, 8)), chunks, "eight batches"
        ),
        Case(
            "chunked-strict/64",
            lambda: list(api.chunked(values, 8, strict=True)),
            chunks,
            "exact strict batches",
        ),
        Case("first/64", lambda: api.first(values), 0, "tuple first"),
        Case(
            "flatten/64",
            lambda: list(api.flatten(chunks)),
            list(values),
            "eight nested tuples",
        ),
        Case("ilen/64", lambda: api.ilen(values), 64, "reusable tuple"),
        Case(
            "ilen-iterator/8192",
            lambda: api.ilen(iter(bulk)),
            len(bulk),
            "fresh one-shot iterator",
        ),
        Case("last/64", lambda: api.last(values), 63, "tuple last"),
        Case("nth/64", lambda: api.nth(values, 31), 31, "tuple index 31"),
        Case(
            "pairwise/64",
            lambda: list(api.pairwise(values)),
            [(index, index + 1) for index in range(63)],
            "stdlib control",
        ),
        Case(
            "partition/64",
            partition,
            (list(range(0, 64, 2)), list(range(1, 64, 2))),
            "both sides completely drained",
        ),
        Case(
            "runs/64",
            lambda: list(api.runs(duplicates, lambda value: value)),
            [(index, (index,) * 4) for index in range(16)],
            "sixteen adjacent runs",
        ),
        Case(
            "take/64",
            lambda: api.take(values, 16),
            list(range(16)),
            "materialized prefix",
        ),
        Case(
            "unique/64",
            lambda: list(api.unique_everseen(duplicates)),
            list(range(16)),
            "hashable duplicates",
        ),
        Case(
            "unique-keyed/64",
            lambda: list(api.unique_everseen(duplicates, key=lambda value: value // 2)),
            list(range(0, 16, 2)),
            "hashable projected keys",
        ),
        Case(
            "unique-justseen/64",
            lambda: list(api.unique_justseen(duplicates)),
            list(range(16)),
            "adjacent duplicates",
        ),
        Case(
            "windowed/64",
            lambda: list(api.windowed(values, 4)),
            windows,
            "full narrow sliding windows",
        ),
        Case(
            "windowed-pairs/64",
            lambda: list(api.windowed(values, 2)),
            [(index, index + 1) for index in range(63)],
            "adjacent windows through the public windowed API",
        ),
        Case(
            "windowed-stride3/64",
            lambda: list(api.windowed(values, 4, 3)),
            windows[::3],
            "overlapping stride",
        ),
        Case(
            "chunked/8192",
            lambda: list(api.chunked(bulk, 8)),
            [bulk[start : start + 8] for start in range(0, len(bulk), 8)],
            "1024 batches",
        ),
        Case(
            "chunked-strict/8192",
            lambda: list(api.chunked(bulk, 8, strict=True)),
            [bulk[start : start + 8] for start in range(0, len(bulk), 8)],
            "1024 exact strict batches",
        ),
        Case(
            "windowed-stride64/8192",
            lambda: list(api.windowed(bulk, 4, 64)),
            [bulk[start : start + 4] for start in range(0, len(bulk) - 3, 64)],
            "sparse windows across discarded gaps",
        ),
        Case(
            "windowed-stride3/8192",
            lambda: list(api.windowed(bulk, 4, 3)),
            [bulk[start : start + 4] for start in range(0, len(bulk) - 3, 3)],
            "overlapping windows across a large input",
        ),
        Case(
            "unique-keyed/8192",
            lambda: list(api.unique_everseen(bulk, key=lambda value: value // 4)),
            list(range(0, len(bulk), 4)),
            "projected hashable keys across a large input",
        ),
    ]

    def first_window(width: int) -> tuple[int, ...]:
        return cast("tuple[int, ...]", next(api.windowed(iter(bulk), width)))

    cases.extend(
        Case(
            f"windowed-first/{width}",
            partial(first_window, width),
            bulk[:width],
            "construct and pull only the first wide window",
        )
        for width in (128, 4096)
    )

    groups = tuple(
        tuple((index + group * 7) % 97 for index in range(size))
        for group, size in enumerate((512, 128, 700, 64, 0))
    )
    expected = _round_robin(groups)[:64]

    def prefix() -> list[int]:
        if eager_prefix:
            # Message selection shape: pad, flatten, remove padding, materialize
            # every item, then slice. Fixtures contain no real None.
            return [
                value
                for row in zip_longest(*groups)
                for value in row
                if value is not None
            ][:64]
        values = (
            value for value in api.flatten(zip_longest(*groups)) if value is not None
        )
        return cast("list[int]", api.take(values, 64))

    cases.append(
        Case(
            "composition/round-robin-take64",
            prefix,
            expected,
            "fixed synthetic groups; baseline zip_longest/filter materialization then slice"
            if eager_prefix
            else "bounded flatten(zip_longest)/filter/take prefix",
        )
    )
    return cases


def _batch(run: Callable[[], Any], count: int) -> dict[str, int]:
    cpu, wall = time.thread_time_ns(), time.perf_counter_ns()
    for _ in range(count):
        run()
    return {
        "wall_ns": time.perf_counter_ns() - wall,
        "cpu_ns": time.thread_time_ns() - cpu,
    }


def _paired(before: Case, after: Case, rounds: int) -> dict[str, Any]:
    before.check()
    after.check()
    cases = {"baseline": before, "candidate": after}
    count, samples = 1, []
    enabled = gc.isenabled()
    gc.disable()
    try:
        while True:
            elapsed = [_batch(case.run, count)["wall_ns"] for case in cases.values()]
            if (
                min(elapsed) >= 5_000_000
                or max(elapsed) >= 250_000_000
                or count >= 1 << 20
            ):
                break
            count *= 2
        for index in range(rounds):
            order = (
                ["baseline", "candidate"]
                if index % 2 == 0
                else ["candidate", "baseline"]
            )
            sample: dict[str, Any] = {"order": order}
            for label in order:
                sample[label] = _batch(cases[label].run, count)
            samples.append(sample)
    finally:
        if enabled:
            gc.enable()
    before.check()
    after.check()
    medians = {
        label: median(sample[label]["wall_ns"] / count for sample in samples)
        for label in cases
    }
    return {
        "iterations": count,
        "samples": samples,
        "median_wall_ns": medians,
        "speedup": medians["baseline"] / medians["candidate"],
        "notes": {label: case.note for label, case in cases.items()},
        "oracle_passed": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-source", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.rounds < 3:
        parser.error("at least three rounds are required")
    sources = {
        "baseline": args.compare_source.resolve(),
        "candidate": args.source.resolve(),
    }
    hashes = {label: _source_hashes(source) for label, source in sources.items()}
    cases = {
        label: _cases(_load(source, label), eager_prefix=label == "baseline")
        for label, source in sources.items()
    }
    measurements = {}
    for before, after in zip(cases["baseline"], cases["candidate"], strict=True):
        if before.name != after.name:
            message = "paired workload identities differ"
            raise ValueError(message)
        measurements[before.name] = _paired(before, after, args.rounds)
    if hashes != {label: _source_hashes(source) for label, source in sources.items()}:
        message = "iterator source changed during measurement"
        raise RuntimeError(message)
    report = {
        "sources": {label: str(source) for label, source in sources.items()},
        "source_hashes": hashes,
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": {
            "python": sys.version,
            "executable": str(Path(sys.executable).resolve()),
            "executable_sha256": sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "compiler": platform.python_compiler(),
            "config_args": sysconfig.get_config_var("CONFIG_ARGS"),
        },
        "rounds": args.rounds,
        "measurements": measurements,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Retained {len(measurements)} paired iterator cases in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
