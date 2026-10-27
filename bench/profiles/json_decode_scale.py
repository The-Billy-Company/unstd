"""Output-checked JSON decoding across the former 65,536-value boundary.

Save the original ``src/unstd`` tree, including its native extension, and run::

    python -m bench.profiles.json_decode_scale \
        --compare-source /tmp/before/src --source src --require-native \
        --rounds 5 --repetitions 3 --output /tmp/json-decode-scale.json

This profile reuses jsonx_throughput's retained-source loading, exact type/float
bit/key-order checks, native qualification, and adjacent alternating wall-clock
batches. Every public call allocates its complete result. Fixture construction,
hashing and verification happen outside timing. Long text cases cover ASCII,
Latin-1, BMP and supplementary-plane Python string storage. The NDJSON case is
explicitly one large array-valued record, rather than many small records.
Use ``--include`` to select a case-name substring.
"""

from __future__ import annotations

import argparse
from functools import partial
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
from typing import TYPE_CHECKING, Any

from bench.core import timing
from bench.profiles import jsonx_throughput as throughput


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


_FIXTURES: dict[str, str] = {}


def _load(function: Callable[..., Any], document: str) -> Any:
    """Include creation and reading of an ordinary in-memory text stream."""
    return function(io.StringIO(document))


def _ndjson_reference(document: str) -> list[Any]:
    return [json.loads(line) for line in document.split("\n") if line.strip()]


def _workloads() -> list[throughput.Workload]:
    """Independent records and precision traps with deterministic source bytes."""
    from unstd.serde import jsonx, ndjson

    cases: list[throughput.Workload] = []

    def add(
        name: str,
        document: str | bytes,
        function: Callable[..., Any] = jsonx.loads,
        reference: Callable[..., Any] = json.loads,
    ) -> None:
        raw = document.encode() if isinstance(document, str) else document
        _FIXTURES[name] = sha256(raw).hexdigest()
        cases.append(
            throughput.Workload(
                name,
                partial(function, document),
                partial(reference, document),
                len(raw),
            )
        )

    small = json.dumps({"id": 7, "text": "small", "ok": True, "items": [1, 2]})
    add("loads/small/str", small)
    add("loads/small/bytes", small.encode())
    for count in (65_534, 65_535, 100_000):
        # The outer mapping contributes two values to the former proof budget.
        value = {"padding": "x" * 2050, "items": list(range(count))}
        add(f"loads/integers-{count}", json.dumps(value, separators=(",", ":")))

    records_document = ""
    for count in (20_000, 100_000):
        records = [
            {
                "id": index,
                "text": f'event-{index}: punctuation , : \\" and number 1e20',
                "active": index % 7 != 0,
            }
            for index in range(count)
        ]
        document = json.dumps(records, separators=(",", ":"))
        add(f"loads/records-{count}", document)
        if count == 20_000:
            records_document = document

    for label, number in (
        ("float", "0.84551240822557006"),
        ("signed-zero", "-0.0"),
        ("big-integer", "18446744073709551616"),
    ):
        document = records_document[:-1] + ',{"number":' + number + "}]"
        add(f"loads/late-{label}", document)

    for label, pattern in (
        ("ascii", 'letters , : \\" 1e20 [ ] { } '),
        ("latin1", 'café, naïve : \\" 1e20 '),
        ("bmp", '汉字, λ : \\" 1e20 '),
        ("ucs4", '🙂, café : \\" 1e20 '),
    ):
        document = json.dumps({"text": pattern * 40_000}, ensure_ascii=False)
        add(f"loads/text-{label}", document)

    add(
        "load/records-20000",
        records_document,
        partial(_load, jsonx.load),
        partial(_load, json.load),
    )
    add(
        "ndjson.loads/array-valued-record-20000",
        records_document + "\n",
        ndjson.loads,
        _ndjson_reference,
    )
    return cases


def _summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize every retained neighboring batch without dropping slow rounds."""
    comparisons = {}
    names = set(runs[0]["timings"])
    if any(set(run["timings"]) != names for run in runs):
        message = "comparison workers measured different workloads"
        raise RuntimeError(message)
    for name in sorted(names):
        timings = {
            label: [run["timings"][name] for run in runs if run["label"] == label]
            for label in ("before", "after")
        }
        batches: list[dict[str, Any]] = []
        for run in runs:
            if run["label"] == "after":
                batches.extend(
                    {"repetition": run["repetition"], **pair}
                    for pair in run["timings"][name]["paired_batches"]
                )
        before = median(row["candidate"]["per_op_ns"] for row in timings["before"])
        after = median(row["candidate"]["per_op_ns"] for row in timings["after"])
        ratio = median(pair["after_over_before"] for pair in batches)
        comparisons[name] = {
            "before_ns": before,
            "after_ns": after,
            "before_over_after": before / after,
            "paired_after_over_before": ratio,
            "paired_speedup": 1 / ratio,
            "batch_pairs": batches,
            "after_mib_s": median(row["candidate_mib_s"] for row in timings["after"]),
            "before_stdlib_ns": median(
                row["stdlib"]["per_op_ns"] for row in timings["before"]
            ),
            "after_stdlib_ns": median(
                row["stdlib"]["per_op_ns"] for row in timings["after"]
            ),
        }
    return comparisons


def main(argv: Sequence[str] | None = None) -> None:
    """Measure the same public operations against retained before/after modules."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("src"))
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--include", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--max-slowdown", type=float)
    parser.add_argument("--paired-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--worker-repetition", type=int, default=0, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    if args.rounds < 1 or args.repetitions < 1:
        parser.error("rounds and repetitions must be positive")
    if args.max_slowdown is not None and (
        not math.isfinite(args.max_slowdown) or args.max_slowdown < 1
    ):
        parser.error("--max-slowdown must be finite and at least one")
    if args.max_slowdown is not None and (
        args.compare_source is None or args.output is None
    ):
        parser.error("--max-slowdown requires --compare-source and --output")
    # Each fresh worker installs this fixture factory before loading either
    # source. The original throughput profile and its command line stay intact.
    throughput._workloads = _workloads
    if args.paired_worker:
        if args.compare_source is None:
            parser.error("--paired-worker requires --compare-source")
        result = throughput._paired_worker(
            args.compare_source.resolve(),
            args.source.resolve(),
            args.rounds,
            args.include,
            args.worker_repetition,
            require_native=args.require_native,
        )
        for row in result:
            row["fixture_sha256"] = _FIXTURES
        print(json.dumps(result))
        return
    if args.compare_source is None:
        parser.error("--compare-source is required for adjacent before/after timing")

    driver_hash = sha256(Path(__file__).read_bytes()).hexdigest()
    inherited_hash = sha256(Path(throughput.__file__).read_bytes()).hexdigest()
    runs = []
    for repetition in range(args.repetitions):
        command = [
            sys.executable,
            "-m",
            "bench.profiles.json_decode_scale",
            "--paired-worker",
            "--compare-source",
            str(args.compare_source.resolve()),
            "--source",
            str(args.source.resolve()),
            "--rounds",
            str(args.rounds),
            "--include",
            args.include,
            "--worker-repetition",
            str(repetition),
        ]
        if args.require_native:
            command.append("--require-native")
        rows = json.loads(
            subprocess.check_output(  # noqa: S603 - fixed interpreter and module
                command,
                text=True,
                env=dict(
                    os.environ,
                    PYTHONPATH=os.pathsep.join(
                        (
                            str(args.source.resolve()),
                            str(Path(__file__).resolve().parents[2]),
                        )
                    ),
                ),
            )
        )
        if (
            not isinstance(rows, list)
            or len(rows) != 2
            or {row["label"] for row in rows} != {"before", "after"}
            or any(row["repetition"] != repetition for row in rows)
        ):
            message = "paired worker did not retain both revision results"
            raise RuntimeError(message)
        runs.extend(rows)
        print(f"Measured paired repetition {repetition + 1}", file=sys.stderr)

    for run in runs:
        first = next(row for row in runs if row["label"] == run["label"])
        if (
            run["environment"] != runs[0]["environment"]
            or run["provenance"] != first["provenance"]
            or run["fixture_sha256"] != runs[0]["fixture_sha256"]
        ):
            message = "environment, source, binary or fixtures changed between workers"
            raise RuntimeError(message)
    if (
        sha256(Path(__file__).read_bytes()).hexdigest() != driver_hash
        or sha256(Path(throughput.__file__).read_bytes()).hexdigest() != inherited_hash
    ):
        message = "benchmark driver changed during measurement"
        raise RuntimeError(message)
    comparisons = _summary(runs)
    regressions = [
        name
        for name, row in comparisons.items()
        if args.max_slowdown is not None
        and row["paired_after_over_before"] > args.max_slowdown
    ]
    report = {
        "driver_sha256": driver_hash,
        "timing_harness_sha256": inherited_hash,
        "estimator": "median of all adjacent after/before wall-clock batch mean ratios; alternating order on one thread",
        "calibration": {
            "target_batch_ns": throughput.BATCH_TARGET_NS,
            "count_probe_estimator": "minimum of three calibration probes",
            "max_batch_ns": timing._MAX_BATCH_NS,
            "measured_rounds": "all retained",
        },
        "comparisons": comparisons,
        "runs": runs,
        "require_native": args.require_native,
        "max_slowdown": args.max_slowdown,
        "regressions": regressions,
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)
    if regressions:
        message = f"JSON decode slowdown exceeds {args.max_slowdown}: {regressions}"
        raise RuntimeError(message)


if __name__ == "__main__":
    main()
