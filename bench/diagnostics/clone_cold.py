"""Public clone latency with class-admission caches cold before each operation.

Inputs, imports, interpreter and allocator are warm. ``_NODES``, ``_RECORDS`` and
``_FIELDS`` are cleared outside the measured interval for each implementation
and each repetition. The public copy call and output destruction are timed.
This complements steady-state batches; it is not process-startup measurement.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
from typing import Any

from bench.profiles import clone_common_cpu as common
from bench.profiles import clone_cpu as base


def _clear_caches(module: Any) -> list[str]:
    """Clear only the known admission/field caches, before timer entry."""
    cleared = []
    for name in ("_NODES", "_RECORDS", "_FIELDS"):
        cache = getattr(module, name, None)
        if cache is not None:
            cache.clear()
            cleared.append(name)
    return cleared


def _summarize(samples: list[dict[str, float]]) -> dict[str, Any]:
    """Retain one-operation samples and their measured distributions."""
    return {
        "samples": samples,
        "median_cpu_ns": statistics.median(row["cpu_ns"] for row in samples),
        "median_wall_ns": statistics.median(row["wall_ns"] for row in samples),
        "min_cpu_ns": min(row["cpu_ns"] for row in samples),
        "min_wall_ns": min(row["wall_ns"] for row in samples),
    }


def main() -> None:
    """Compare actual public operations after untimed cache resets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=101)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("rounds must be positive")
    from unstd.clone import structural as current

    modules = {
        "current": current,
        "before": base._snapshot(args.compare_dir.resolve(), "cold_before"),
    }
    selected = {
        "nested-small-5",
        "nested-200",
        "dataclass-small",
        "dataclasses-200",
        "dataclasses-flat-500",
        "asdict-tree-200",
    }
    output: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "rounds": args.rounds,
        "measurement": "one public clone call plus output destruction",
        "cold_state": "known class-admission and field caches",
        "warm_state": "imports, interpreter, allocator and prepared inputs",
        "cache_reset_timed": False,
        "gc_enabled_during_measurement": False,
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
    }
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        for case in common._workloads():
            if case.name not in selected:
                continue
            calls = base._callables(case, modules)
            for label, call in calls.items():
                if not common._check(case, label, call()):
                    message = f"incorrect public output: {case.name}/{label}"
                    raise AssertionError(message)
            samples: dict[str, list[dict[str, float]]] = {label: [] for label in calls}
            cleared: dict[str, list[str]] = {}
            for repetition in range(args.rounds):
                for label in list(calls)[:: 1 if repetition % 2 == 0 else -1]:
                    if label in modules:
                        cleared[label] = _clear_caches(modules[label])
                    samples[label].append(base._batch(calls[label], 1))
            output["workloads"][case.name] = {
                "operation": case.operation,
                "cleared_caches": cleared,
                "timings": {
                    label: _summarize(values) for label, values in samples.items()
                },
            }
        output["empty_call_timer_control"] = _summarize(
            [base._batch(lambda: None, 1) for _ in range(args.rounds)]
        )
    finally:
        if was_enabled:
            gc.enable()
        gc.collect()
    rendered = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
