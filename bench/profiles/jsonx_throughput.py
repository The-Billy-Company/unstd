"""Output-checked JSONX throughput against saved sources and stdlib controls.

Save the original ``src/unstd`` tree, including its built native extension,
then run this unchanged driver against both trees with the same interpreter::

    python -m bench.profiles.jsonx_throughput \
        --compare-source /tmp/before/src --source src \
        --output /tmp/jsonx-throughput.json

Each repetition launches isolated workers in alternating before/after order.
Payload construction and output validation remain outside the timed calls.
Every operation allocates its result; no encoded-result cache is used.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from functools import partial
from hashlib import sha256
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
from typing import TYPE_CHECKING, Any

from bench.core.timing import measure


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Workload:
    """A public operation and an independent stdlib reference."""

    name: str
    call: Callable[[], Any]
    reference: Callable[[], Any]
    input_bytes: int


def _same(actual: Any, expected: Any) -> bool:
    """Retain types, float bits and object key order when checking a decode."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is float:
        return bool(actual.hex() == expected.hex())
    if type(expected) is dict:
        return list(actual) == list(expected) and all(
            _same(actual[key], value) for key, value in expected.items()
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def _workloads() -> list[Workload]:
    from unstd.serde import jsonx

    small = {"id": 7, "name": "small", "ok": True, "values": [1, 2]}
    records = [
        {"id": i, "text": 'café, : \\"', "score": i / 7, "active": i % 2 == 0}
        for i in range(1_000)
    ]
    payloads = {
        "small": small,
        "ascii-records-1k": [
            {"id": i, "text": 'ascii, : \\"', "active": i % 2 == 0}
            for i in range(1_000)
        ],
        "unicode-records-1k": [
            {"id": i, "text": 'café 汉字 🙂, : \\"', "active": i % 2 == 0}
            for i in range(1_000)
        ],
        "integers-10k": list(range(10_000)),
        "integers-100k": list(range(100_000)),
        "wide-floats": {str(i): i * 1.5 for i in range(10_000)},
        "floats-10k": [i / 7 for i in range(10_000)],
        "records-1k": records,
        "text-1m": {"text": 'abcdef, : \\" ' * 80_000},
        "unicode-1m": {"text": 'café 汉字 🙂, : \\" ' * 40_000},
    }
    workloads: list[Workload] = []
    for name, value in payloads.items():
        default = json.dumps(value)
        compact = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        workloads.extend(
            (
                Workload(
                    "dumps/" + name,
                    partial(jsonx.dumps, value),
                    partial(json.dumps, value),
                    len(default.encode()),
                ),
                Workload(
                    "dumpb/" + name,
                    partial(jsonx.dumpb, value),
                    partial(_compact, value),
                    len(compact.encode()),
                ),
            )
        )
        # Explicit escaped and raw Unicode documents exercise distinct inputs.
        for representation, document in (
            ("str", default),
            ("bytes", compact.encode()),
        ):
            workloads.append(
                Workload(
                    f"loads/{name}/{representation}",
                    partial(jsonx.loads, document),
                    partial(json.loads, document),
                    len(document.encode() if type(document) is str else document),
                )
            )
    for name, options in (
        ("indent2/records-1k", {"indent": 2}),
        ("indent4/records-1k", {"indent": 4}),
        ("sorted/records-1k", {"sort_keys": True}),
        ("compact/records-1k", {"separators": (",", ":"), "ensure_ascii": False}),
    ):
        reference = partial(json.dumps, records, **options)
        workloads.append(
            Workload(
                "dumps/" + name,
                partial(jsonx.dumps, records, **options),
                reference,
                len(reference().encode()),
            )
        )
    for name in ("ascii-records-1k", "unicode-records-1k", "integers-100k"):
        value = payloads[name]
        reference = partial(json.dumps, value, indent=2)
        workloads.append(
            Workload(
                "dumps/indent2/" + name,
                partial(jsonx.dumps, value, indent=2),
                reference,
                len(reference().encode()),
            )
        )
    for name in ("canonical", "canonical_strict"):
        reference_bytes = partial(_canonical, records)
        workloads.append(
            Workload(
                name + "/records-1k",
                partial(getattr(jsonx, name), records),
                reference_bytes,
                len(reference_bytes()),
            )
        )
    workloads.append(
        Workload(
            "dump/records-1k",
            partial(_dump, jsonx.dump, records),
            partial(_dump, json.dump, records),
            len(json.dumps(records).encode()),
        )
    )
    return workloads


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _dump(function: Callable[..., Any], value: object) -> str:
    stream = io.StringIO()
    function(value, stream)
    return stream.getvalue()


def _provenance(source: Path, jsonx: Any) -> dict[str, Any]:
    imported = Path(jsonx.__file__).resolve()
    if imported != source / "unstd/serde/jsonx.py":
        message = f"requested {source}, imported {imported}"
        raise RuntimeError(message)
    files = {
        str(path.relative_to(source)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(imported.parent.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".pyi", ".so", ".pyd"}
    }
    native_source = source.parent / "native-serde"
    if not native_source.is_dir():
        native_source = source.parent / "native/serde"
    native_files = {
        str(path.relative_to(native_source)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(native_source.rglob("*"))
        if path.is_file() and path.suffix in {".c", ".h"}
    }
    native = getattr(jsonx, "_native", None)
    dispatch = {}
    for name in ("loads", "dumpb"):
        function = getattr(jsonx, name)
        if native is not None and hasattr(native, "dispatch_ready"):
            dispatch[name] = {
                "bound": native.is_bound(function),
                "ready": native.dispatch_ready(function),
            }
    return {
        "source": str(source),
        "imported_jsonx": str(imported),
        "native_module": getattr(native, "__file__", None),
        "dispatch": dispatch,
        "sha256": files,
        "native_source_sha256": native_files,
    }


def _worker(source: Path, rounds: int, include: str) -> dict[str, Any]:
    sys.path.insert(0, str(source))
    from unstd.serde import jsonx

    provenance = _provenance(source, jsonx)
    results = {}
    for case in _workloads():
        if include and include not in case.name:
            continue
        expected = case.reference()
        if not _same(case.call(), expected):
            message = f"incorrect output: {case.name}"
            raise AssertionError(message)
        candidate = measure(case.call, rounds=rounds)
        control = measure(case.reference, rounds=rounds)
        if not _same(case.call(), expected):
            message = f"incorrect output after timing: {case.name}"
            raise AssertionError(message)
        results[case.name] = {
            "input_bytes": case.input_bytes,
            "identical_output": True,
            "candidate": asdict(candidate),
            "stdlib": asdict(control),
            "candidate_mib_s": case.input_bytes / candidate.per_op_ns * 1e9 / 2**20,
            "stdlib_over_candidate": control.per_op_ns / candidate.per_op_ns,
        }
    return {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "backends": {name: version(name) for name in ("orjson", "msgspec")},
        },
        "provenance": provenance,
        "timings": results,
    }


def main() -> None:
    """Measure unchanged fixtures against isolated source trees."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("src"))
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--include", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.rounds < 1 or args.repetitions < 1:
        parser.error("rounds and repetitions must be positive")
    if args.worker:
        print(json.dumps(_worker(args.source.resolve(), args.rounds, args.include)))
        return
    sources = [("after", args.source.resolve())]
    if args.compare_source is not None:
        sources.insert(0, ("before", args.compare_source.resolve()))
    runs = []
    for repetition in range(args.repetitions):
        for label, source in sources[:: 1 if repetition % 2 == 0 else -1]:
            command = [
                sys.executable,
                "-m",
                "bench.profiles.jsonx_throughput",
                "--worker",
                "--source",
                str(source),
                "--rounds",
                str(args.rounds),
                "--include",
                args.include,
            ]
            result = json.loads(
                subprocess.check_output(  # noqa: S603
                    command,
                    text=True,
                    env=dict(
                        os.environ,
                        PYTHONPATH=os.pathsep.join(
                            (str(source), str(Path(__file__).resolve().parents[2]))
                        ),
                    ),
                )
            )
            runs.append({"label": label, "repetition": repetition, **result})
            print(f"Measured {label} repetition {repetition + 1}", file=sys.stderr)
    if any(run["environment"] != runs[0]["environment"] for run in runs):
        message = "comparison workers used different environments"
        raise RuntimeError(message)
    comparisons = {}
    if args.compare_source is not None:
        names = set(runs[0]["timings"])
        if any(set(run["timings"]) != names for run in runs):
            message = "comparison workers measured different workloads"
            raise RuntimeError(message)
        for name in sorted(names):
            timings = {
                label: [run["timings"][name] for run in runs if run["label"] == label]
                for label in ("before", "after")
            }
            before_ns = median(
                row["candidate"]["per_op_ns"] for row in timings["before"]
            )
            after_ns = median(row["candidate"]["per_op_ns"] for row in timings["after"])
            comparisons[name] = {
                "before_ns": before_ns,
                "after_ns": after_ns,
                "before_over_after": before_ns / after_ns,
                "after_mib_s": median(
                    row["candidate_mib_s"] for row in timings["after"]
                ),
                "before_stdlib_ns": median(
                    row["stdlib"]["per_op_ns"] for row in timings["before"]
                ),
                "after_stdlib_ns": median(
                    row["stdlib"]["per_op_ns"] for row in timings["after"]
                ),
            }
    report = {
        "driver_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "estimator": "median across repetitions of minimum wall-clock batch means",
        "comparisons": comparisons,
        "runs": runs,
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
