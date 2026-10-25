"""Read-only paired diagnosis of the three frozen random-workload failures.

Candidate timings remain subject to their original absolute ceilings. A slow
reference suggests environment sensitivity; it neither proves machine noise
nor excuses a candidate above its ceiling.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from functools import partial
import gc
import hashlib
import inspect
import json
from pathlib import Path
import random
import secrets
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from bench import speeds


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


ITERATIONS = {
    "unstd.rand.crypto.bits": 32768,
    "unstd.rand.seed": 2048,
    "unstd.rand.shuffle": 8192,
}


@dataclass(frozen=True, slots=True)
class Batch:
    """Unadjusted elapsed wall and calling-thread CPU nanoseconds."""

    iterations: int
    wall_ns: int
    thread_cpu_ns: int

    @property
    def per_op_ns(self) -> float:
        """Wall-clock nanoseconds per operation, without normalization."""
        return self.wall_ns / self.iterations


@dataclass(frozen=True, slots=True)
class Pair:
    """An adjacent alternating-order candidate and independent control pair."""

    repetition: int
    order: tuple[str, str]
    candidate: Batch
    control: Batch


def _batch(fn: Callable[[], object], iterations: int) -> Batch:
    """Retain both clocks around the same invocation loop."""
    cpu_start = time.thread_time_ns()
    wall_start = time.perf_counter_ns()
    for _ in range(iterations):
        fn()
    wall_ns = time.perf_counter_ns() - wall_start
    cpu_ns = time.thread_time_ns() - cpu_start
    return Batch(iterations, wall_ns, cpu_ns)


def controls() -> tuple[dict[str, Callable[[], object]], dict[str, bool]]:
    """Build equivalent controls with independent seedable engines."""
    import numpy as np

    from unstd import rand
    from unstd.rand import crypto

    seeded = random.Random(173)
    shuffled = random.Random(173)
    numpy_engine = np.random.Generator(np.random.PCG64(173))
    population = tuple(range(32))

    def seed() -> None:
        nonlocal numpy_engine
        replacement = np.random.Generator(np.random.PCG64(173))
        seeded.seed(173)
        numpy_engine = replacement

    def shuffle() -> list[int]:
        result = list(population)
        shuffled.shuffle(result)
        return result

    return {
        "unstd.rand.crypto.bits": partial(secrets.randbits, 64),
        "unstd.rand.seed": seed,
        "unstd.rand.shuffle": shuffle,
    }, {
        "bits_is_exact_secrets_primitive": crypto.bits is secrets.randbits,
        "shuffle_is_exact_stdlib_method": getattr(rand.shuffle, "__func__", None)
        is random.Random.shuffle,
    }


def paired(
    cases: Sequence[speeds.Case],
    references: dict[str, Callable[[], object]],
    *,
    repetitions: int = 5,
) -> dict[str, list[Pair]]:
    """Alternate order and retain every raw batch; never retry to hide a failure."""
    if repetitions < 3:
        message = "paired diagnosis requires at least three repetitions"
        raise speeds.RegistryError(message)
    for case in cases:
        case.check(case.run())
        case.check(references[case.name]())
    was_on = gc.isenabled()
    gc.disable()
    results: dict[str, list[Pair]] = {case.name: [] for case in cases}
    try:
        for repetition in range(repetitions):
            order = (
                ("candidate", "control")
                if repetition % 2 == 0
                else ("control", "candidate")
            )
            for case in cases:
                functions = {"candidate": case.run, "control": references[case.name]}
                batches = {
                    name: _batch(functions[name], ITERATIONS[case.name])
                    for name in order
                }
                results[case.name].append(
                    Pair(repetition, order, batches["candidate"], batches["control"])
                )
    finally:
        if was_on:
            gc.enable()
        else:
            gc.disable()
    for case in cases:
        case.check(case.run())
        case.check(references[case.name]())
    return results


def summarize(pairs: Sequence[Pair], frozen: dict[str, Any]) -> dict[str, Any]:
    """Distinguish evidence of host sensitivity from candidate-specific cost."""
    if len(pairs) < 3:
        message = "diagnosis requires at least three paired samples"
        raise speeds.RegistryError(message)
    for pair in pairs:
        for sample in (pair.candidate, pair.control):
            if (
                sample.iterations <= 0
                or sample.wall_ns <= 0
                or sample.thread_cpu_ns < 0
            ):
                message = "invalid raw diagnostic batch"
                raise speeds.RegistryError(message)
    candidate_ns = min(pair.candidate.per_op_ns for pair in pairs)
    control_ns = min(pair.control.per_op_ns for pair in pairs)
    ceiling_ns = float(frozen["ceiling_ns"])
    candidate_to_control = candidate_ns / control_ns
    if candidate_ns <= ceiling_ns:
        classification = "within ceiling"
    elif candidate_to_control > 1.30:
        classification = "candidate slower than control; investigate code"
    elif control_ns > ceiling_ns:
        classification = "reference also slow; environment suspect"
    else:
        classification = "candidate over ceiling; cause unresolved"
    return {
        "classification": classification,
        "within_frozen_ceiling": candidate_ns <= ceiling_ns,
        "frozen_measured_ns": frozen["measured_ns"],
        "frozen_ceiling_ns": frozen["ceiling_ns"],
        "candidate_best_wall_ns": candidate_ns,
        "control_best_wall_ns": control_ns,
        "candidate_to_control": candidate_to_control,
        "pairs": [asdict(pair) for pair in pairs],
    }


def source_evidence() -> dict[str, object]:
    """Record loaded sources and whether their bytes match the current Git HEAD."""
    from unstd import rand
    from unstd.rand import crypto, draw

    git = shutil.which("git")
    if git is None:
        message = "Git is required for unchanged-source diagnosis evidence"
        raise speeds.RegistryError(message)
    sources: dict[str, object] = {}
    for relative, module in (
        ("rand/draw.py", draw),
        ("rand/crypto.py", crypto),
        ("rand/__init__.py", rand),
    ):
        loaded = Path(inspect.getfile(module)).resolve()
        current = loaded.read_bytes()
        head = subprocess.run(  # noqa: S603 — fixed read-only Git command
            [git, "show", f"HEAD:src/unstd/{relative}"],
            cwd=speeds.ROOT,
            capture_output=True,
            check=False,
        )
        sources[relative] = {
            "loaded_path": str(loaded),
            "sha256": hashlib.sha256(current).hexdigest(),
            "head_sha256": hashlib.sha256(head.stdout).hexdigest()
            if head.returncode == 0
            else None,
            "matches_head": current == head.stdout if head.returncode == 0 else None,
        }
    return sources


def _select(cases: Sequence[speeds.Case]) -> list[speeds.Case]:
    """Select exactly the original three workload contracts."""
    selected = [case for case in cases if case.name in ITERATIONS]
    if {case.name for case in selected} != ITERATIONS.keys():
        message = "the original random diagnosis workloads are missing"
        raise speeds.RegistryError(message)
    return selected


def _protect_baseline(output: Path, baseline: Path) -> None:
    """Refuse output paths or hard links that would overwrite frozen evidence."""
    if output.resolve() == baseline.resolve() or (
        output.exists() and baseline.exists() and output.samefile(baseline)
    ):
        message = "diagnosis output must not overwrite the frozen baseline"
        raise speeds.RegistryError(message)


def main(argv: list[str] | None = None) -> int:
    """Write diagnosis evidence, preserving an above-ceiling result as failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--baseline", type=Path, default=speeds.BASELINE)
    args = parser.parse_args(argv)
    try:
        _protect_baseline(args.output, args.baseline)
        baseline = speeds.load_baseline(args.baseline)
        with speeds.registry() as cases:
            speeds.validate_baseline(cases, baseline)
            selected = _select(cases)
            references, identities = controls()
            samples = paired(selected, references, repetitions=args.repetitions)
            rows = {
                case.name: summarize(samples[case.name], baseline["cases"][case.name])
                for case in selected
            }
            current_environment = speeds.environment()
            report = {
                "baseline_sha256": hashlib.sha256(
                    args.baseline.read_bytes()
                ).hexdigest(),
                "environment": current_environment,
                "environment_matches_baseline": current_environment
                == baseline["environment"],
                "source_evidence": source_evidence(),
                "primitive_identities": identities,
                "cases": rows,
                "interpretation": "Controls identify possible environment sensitivity; they do not prove noise or normalize candidate speeds. Every raw candidate remains subject to its frozen absolute ceiling.",
            }
            args.output.write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            for name, row in rows.items():
                print(
                    f"{name}: {row['candidate_best_wall_ns']:.1f} ns / ceiling {row['frozen_ceiling_ns']:.1f} ns; control {row['control_best_wall_ns']:.1f} ns; {row['classification']}"
                )
            if not report["environment_matches_baseline"]:
                return 2
            return int(any(not row["within_frozen_ceiling"] for row in rows.values()))
    except (OSError, speeds.RegistryError, ImportError) as error:
        print(f"rand diagnosis: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
