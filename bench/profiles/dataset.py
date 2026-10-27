"""Compare indexed snapshots with eager and streaming synthetic JSONL reads.

Run ``python -m bench.profiles.dataset --output bench/reports/dataset/measured.json``.
Each operation runs in a fresh process. Wall timing, traced allocations and
decode-count instrumentation use separate passes; every pass verifies its
output against independently generated values. No customer data is read.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import gc
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from itertools import chain, islice
import json
import os
from pathlib import Path
import platform
import random
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import tracemalloc
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from unstd.dataset import Dataset, build
from unstd.serde import jsonx, ndjson


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from unstd.serde.jsonx import Json


_OPERATIONS = (
    "build",
    "eager-prefix",
    "stream-prefix",
    "dataset-prefix",
    "eager-random",
    "stream-random",
    "dataset-random",
    "eager-scan",
    "lines-scan",
    "stream-scan",
    "dataset-scan",
    "dataset-batch-scan",
)


def _public_origin(path: Path) -> str:
    """A reproducible origin label without machine-specific directory prefixes."""
    parts = path.parts
    if "site-packages" in parts:
        return "/".join(parts[parts.index("site-packages") :])
    root = Path(__file__).resolve().parents[2]
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None and not relative.parts[0].startswith(".local"):
        return relative.as_posix()
    if "unstd" in parts:
        package = len(parts) - 1 - tuple(reversed(parts)).index("unstd")
        return "installed/" + "/".join(parts[package:])
    return f"python/{path.name}"


def _provenance() -> dict[str, Any]:
    """Hashes of imported product helpers and engines, plus backend versions."""
    names = {
        "dataset": "unstd.dataset",
        "jsonx": "unstd.serde.jsonx",
        "ndjson": "unstd.serde.ndjson",
        "fs": "unstd.fs",
        "serde_encoding": "unstd.serde.encoding",
        "serde_recursion": "unstd.serde.recursion",
        "unstd": "unstd",
        "extras": "unstd.extras",
        "json_native": "unstd.serde._json_native",
        "fs_native": "unstd.fs._fs_native",
        "orjson_native": "orjson.orjson",
        "msgspec_native": "msgspec._core",
    }
    for name in sorted(sys.modules):
        if name.startswith("unstd.dataset."):
            names["dataset_" + name.removeprefix("unstd.dataset.")] = name
    files: dict[str, Any] = {}
    for label, name in names.items():
        module = sys.modules.get(name)
        origin = getattr(module, "__file__", None)
        if origin is None:
            files[label] = None
            continue
        path = Path(origin).resolve()
        files[label] = {
            "path": _public_origin(path),
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    proof = Path(__file__).resolve()
    files["proof"] = {
        "path": _public_origin(proof),
        "sha256": sha256(proof.read_bytes()).hexdigest(),
    }
    versions: dict[str, str | None] = {}
    for distribution in ("orjson", "msgspec", "numpy"):
        try:
            versions[distribution] = version(distribution)
        except PackageNotFoundError:
            versions[distribution] = None
    return {"files": files, "backend_versions": versions}


def _row(index: int) -> dict[str, Json]:
    """A deterministic nested JSON record for document and evaluation workloads."""
    name = ("calendar_search", "mail_search", "read_page", "save_note")[index % 4]
    prompt = sha256(f"synthetic-corpus:{index}".encode()).hexdigest() * 7
    return {
        "id": f"example-{index:08d}",
        "source": "synth",
        "track": f"t{1 + index % 4}",
        "tool_name": name,
        "messages": [
            {"role": "system", "content": "Use the available tools when needed."},
            {"role": "user", "content": prompt},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": name,
                            "arguments": json.dumps({"query": f"synthetic-{index}"}),
                        }
                    }
                ],
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ],
        "tokens": 64 + index * 17 % 4096,
        "passed": index % 7 != 0,
    }


def _digest(value: Json) -> str:
    """A canonical output identity computed outside timed operations."""
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _aggregate(records: Iterator[Json]) -> Json:
    """Full-scan aggregate checked against arithmetic, independently of decoding."""
    rows = tokens = passed = prompt_chars = 0
    for value in records:
        row = cast("dict[str, Json]", value)
        messages = cast("list[dict[str, Json]]", row["messages"])
        rows += 1
        tokens += cast("int", row["tokens"])
        passed += cast("bool", row["passed"])
        prompt_chars += len(cast("str", messages[1]["content"]))
    return {
        "rows": rows,
        "tokens": tokens,
        "passed": passed,
        "prompt_chars": prompt_chars,
    }


def _prepare(directory: Path, rows: int, prefix: int, samples: int) -> dict[str, Any]:
    """Materialize only synthetic JSONL and independent expected selections."""
    indices = random.Random(1729).sample(range(rows), min(rows, samples))
    with (directory / "records.jsonl").open("wb") as stream:
        for index in range(rows):
            stream.write(
                json.dumps(_row(index), separators=(",", ":")).encode() + b"\n"
            )
    config = {"rows": rows, "prefix": min(rows, prefix), "indices": indices}
    expected: dict[str, Json] = {
        "build": {"rows": rows},
        "prefix": [_row(index) for index in range(min(rows, prefix))],
        "random": [_row(index) for index in indices],
        "scan": {
            "rows": rows,
            "tokens": sum(64 + index * 17 % 4096 for index in range(rows)),
            "passed": rows - len(range(0, rows, 7)),
            "prompt_chars": rows * 448,
        },
    }
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (directory / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    return {
        **config,
        "jsonl_bytes": (directory / "records.jsonl").stat().st_size,
        "expected_sha256": {key: _digest(value) for key, value in expected.items()},
    }


def _operation(directory: Path, operation: str, config: dict[str, Any]) -> Json:
    """One complete application read, including opening and closing resources."""
    source = directory / "records.jsonl"
    artifact = directory / "records.dataset"
    prefix = cast("int", config["prefix"])
    indices = cast("list[int]", config["indices"])
    if operation == "build":
        with source.open("rb") as stream:
            return {"rows": build(artifact, ndjson.iter_loads(stream))}
    lane, shape = operation.split("-", 1)
    if lane == "eager":
        # Materialize all records before selecting the requested rows.
        records: list[Json] = [
            json.loads(line) for line in source.read_text().splitlines() if line.strip()
        ]
        if shape == "prefix":
            return records[:prefix]
        if shape == "random":
            return [records[index] for index in indices]
        return _aggregate(iter(records))
    if lane == "lines":
        # Retain source lines to obtain a total, then decode during the scan.
        lines = [line for line in source.read_text().splitlines() if line.strip()]
        return _aggregate(json.loads(line) for line in lines)
    if lane == "dataset":
        with Dataset(artifact) as dataset:
            if len(dataset) != config["rows"]:
                message = "dataset row count differs from fixture"
                raise AssertionError(message)
            if shape == "prefix":
                return dataset[:prefix]
            if shape == "random":
                return dataset.take(indices)
            if shape == "batch-scan":
                return _aggregate(chain.from_iterable(dataset.batches(1024)))
            return _aggregate(iter(dataset))
    with source.open("rb") as stream:
        parsed = ndjson.iter_loads(stream)
        if shape == "prefix":
            return list(islice(parsed, prefix))
        if shape == "random":
            # A fair one-pass scan, retaining only the requested records.
            wanted = set(indices)
            selected: dict[int, Json] = {}
            for index, row in enumerate(parsed):
                if index in wanted:
                    selected[index] = row
                    if len(selected) == len(wanted):
                        break
            return [selected[index] for index in indices]
        return _aggregate(parsed)


@contextmanager
def _count_decodes(operation: str, count: Callable[[], None]) -> Iterator[None]:
    """Count actual decode calls in a separate untimed verification pass."""
    if operation == "dataset-batch-scan":
        original_batch = ndjson.loads

        def batch(raw: str | bytes) -> Any:
            rows = original_batch(raw)
            for _row in rows:
                count()
            return rows

        with patch.object(ndjson, "loads", batch):
            yield
        return
    if operation.startswith(("eager-", "lines-")):
        original = json.loads

        def eager(raw: str | bytes, *args: Any, **kwargs: Any) -> Any:
            count()
            return original(raw, *args, **kwargs)

        with patch.object(json, "loads", eager):
            yield
        return
    parse = jsonx.loads

    def decoded(raw: str | bytes) -> Any:
        count()
        return parse(raw)

    with patch.object(jsonx, "loads", decoded), patch.object(ndjson, "_parse", decoded):
        yield


def _rss_bytes() -> int | None:
    """Process lifetime peak RSS where resource units are documented here."""
    if sys.platform not in {"darwin", "linux"}:
        return None
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _worker(directory: Path, operation: str, rounds: int) -> dict[str, Any]:
    """Measure one operation in an isolated process, verifying every pass."""
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    provenance = _provenance()
    pinned = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    if provenance != pinned:
        message = f"{operation} imported different product sources or backends"
        raise AssertionError(message)
    shape = "build" if operation == "build" else operation.split("-")[-1]
    target = expected[shape]
    samples = []
    for _ in range(rounds):
        gc.collect()
        start = time.perf_counter()
        value = _operation(directory, operation, config)
        samples.append(time.perf_counter() - start)
        if value != target:
            message = (
                f"{operation} timing output differs from independent expected rows"
            )
            raise AssertionError(message)
        del value
    timing_rss = _rss_bytes()
    gc.collect()
    tracemalloc.start()
    value = _operation(directory, operation, config)
    _, allocation_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if value != target:
        message = f"{operation} allocation output differs from expected rows"
        raise AssertionError(message)
    del value
    decoded_rows = 0

    def counted() -> None:
        nonlocal decoded_rows
        decoded_rows += 1

    with _count_decodes(operation, counted):
        value = _operation(directory, operation, config)
    if value != target:
        message = f"{operation} instrumented output differs from expected rows"
        raise AssertionError(message)
    expected_decodes = config["rows"]
    if operation.endswith("-prefix") and not operation.startswith("eager-"):
        expected_decodes = config["prefix"]
    elif operation == "dataset-random":
        expected_decodes = len(config["indices"])
    elif operation == "stream-random":
        expected_decodes = max(config["indices"]) + 1
    if decoded_rows != expected_decodes:
        message = f"{operation} decoded {decoded_rows}; expected {expected_decodes}"
        raise AssertionError(message)
    mapping_type: str | None = None
    if operation.startswith("dataset-"):
        # Observe the actual resource owner outside every measured/instrumented
        # pass. A loaded native module alone does not prove native mapping use.
        with Dataset(directory / "records.dataset") as dataset:
            mapped = dataset._store.opened()
            mapping_type = f"{type(mapped).__module__}.{type(mapped).__qualname__}"
    if _provenance() != provenance:
        message = f"{operation} source or backend drifted during execution"
        raise AssertionError(message)
    return {
        "pid": os.getpid(),
        "wall_seconds": samples,
        "median_wall_seconds": median(samples),
        "peak_traced_python_bytes": allocation_peak,
        "timing_process_peak_rss_bytes": timing_rss,
        "decoded_rows": decoded_rows,
        "output_sha256": _digest(value),
        "verified": True,
        "provenance_verified": True,
        "observed_mapping_type": mapping_type,
    }


def _launch(directory: Path, operation: str, rounds: int) -> dict[str, Any]:
    """A bounded fresh process using the same interpreter and source import."""
    result = subprocess.run(  # noqa: S603 — owned interpreter and literal module
        [
            sys.executable,
            "-m",
            "bench.profiles.dataset",
            "--worker-directory",
            str(directory),
            "--operation",
            operation,
            "--rounds",
            str(rounds),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return cast("dict[str, Any]", json.loads(result.stdout))


def main() -> None:
    """Write verified measurements, including cold cost and losing comparisons."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rows", type=int, default=25_000)
    parser.add_argument("--prefix", type=int, default=32)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--worker-directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--operation", choices=_OPERATIONS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_directory is not None:
        if args.operation is None:
            parser.error("worker operation is required")
        print(json.dumps(_worker(args.worker_directory, args.operation, args.rounds)))
        return
    if min(args.rows, args.prefix, args.samples, args.rounds) <= 0:
        parser.error("rows, prefix, samples and rounds must be positive")
    provenance = _provenance()
    with TemporaryDirectory(prefix="unstd-dataset-proof-") as temporary:
        directory = Path(temporary)
        fixture = _prepare(directory, args.rows, args.prefix, args.samples)
        (directory / "provenance.json").write_text(
            json.dumps(provenance), encoding="utf-8"
        )
        results = {
            operation: _launch(directory, operation, args.rounds)
            for operation in _OPERATIONS
        }
        artifact = directory / "records.dataset"
        artifact_bytes = artifact.stat().st_size
        with artifact.open("rb") as stream:
            artifact_magic = stream.read(8).hex()
    if _provenance() != provenance:
        message = "product source or backend drifted during the proof; rerun required"
        raise AssertionError(message)
    comparisons: dict[str, Any] = {}
    build_seconds = results["build"]["median_wall_seconds"]
    for shape in ("prefix", "random", "scan"):
        candidate = results[f"dataset-{shape}"]
        comparisons[shape] = {}
        for lane in (
            ("eager", "stream", "lines") if shape == "scan" else ("eager", "stream")
        ):
            baseline = results[f"{lane}-{shape}"]
            saving = baseline["median_wall_seconds"] - candidate["median_wall_seconds"]
            comparisons[shape][lane] = {
                "dataset_speedup": baseline["median_wall_seconds"]
                / candidate["median_wall_seconds"],
                "python_allocation_reduction": baseline["peak_traced_python_bytes"]
                / candidate["peak_traced_python_bytes"],
                "build_amortization_reads": (
                    int(build_seconds // saving) + 1 if saving > 0 else None
                ),
            }
    candidate = results["dataset-batch-scan"]
    comparisons["batch_scan"] = {}
    for lane in ("eager", "stream", "lines"):
        baseline = results[f"{lane}-scan"]
        saving = baseline["median_wall_seconds"] - candidate["median_wall_seconds"]
        comparisons["batch_scan"][lane] = {
            "dataset_speedup": baseline["median_wall_seconds"]
            / candidate["median_wall_seconds"],
            "python_allocation_reduction": baseline["peak_traced_python_bytes"]
            / candidate["peak_traced_python_bytes"],
            "build_amortization_reads": (
                int(build_seconds // saving) + 1 if saving > 0 else None
            ),
        }
    report = {
        "schema": 2,
        "captured_at": datetime.now(UTC).isoformat(),
        "command": (
            "python -m bench.profiles.dataset "
            f"--rows {args.rows} --prefix {args.prefix} --samples {args.samples} "
            f"--rounds {args.rounds} --output PATH"
        ),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "backend_versions": provenance["backend_versions"],
            "dataset_kernel_version": getattr(
                sys.modules.get("unstd.dataset._dataset_native"), "KERNEL_VERSION", None
            ),
        },
        "source_sha256": {
            key: value["sha256"] if value is not None else None
            for key, value in provenance["files"].items()
        },
        "import_origins": {
            key: value["path"] if value is not None else None
            for key, value in provenance["files"].items()
        },
        "provenance_verified_before_and_after": True,
        "fixture": fixture,
        "artifact_bytes": artifact_bytes,
        "artifact_magic_hex": artifact_magic,
        "batch_size": 1024,
        "artifact_to_jsonl_ratio": artifact_bytes / fixture["jsonl_bytes"],
        "results": results,
        "comparisons": comparisons,
        "limits": [
            "Synthetic nested JSON records, one machine, filesystem cache not flushed; no service latency claim.",
            "Each operation has a fresh process; imports/process startup, fixture generation, correctness comparison and output hashing are outside timers.",
            "Every read includes resource opening and closing; mapped-format geometry validation and eager materialization are timed.",
            "Three default timing passes are separate from one tracemalloc and one decode-count pass; decode counters observe actual calls.",
            "Tracemalloc measures Python allocations only; process lifetime peak RSS includes imports, expected selections and timing passes, excludes the later traced pass.",
            "Build includes streaming JSONL decoding, row encoding, offset spooling, SHA256 content/index checksumming, file fsync and atomic replacement.",
            "Amortization includes measured build cost; repeated read savings assume the same artifact is reused and exclude one-time source generation.",
            "Full-scan output verifies aggregate fields; selected prefix/random outputs compare complete independently generated rows. Random selection uses Dataset.take; bounded batch scans use Dataset.batches(1024).",
            "Single-row paths count actual decoder calls; the fused bounded-batch path counts rows returned by each observed NDJSON batch decoder invocation in a separate verification pass.",
            "The actual mapping owner type is observed separately after timing/tracing for every Dataset operation; a loaded native module alone is not treated as routing evidence.",
            "Imported helper/native file hashes and backend versions are pinned before fixture generation and checked in each worker before/after execution and again after the whole proof; drift refuses publication.",
        ],
    }
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
