"""Paired gzip read workloads, including streaming consumption shapes.

Run ``python bench/gzip_reads.py --baseline-source /saved/src --output reads.json``.
Repeat ``--fixture`` for captured payloads; their original bytes are never rewritten.
Each variant lives in its own process; timing excludes process startup and IPC.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import gc
import gzip
import hashlib
import importlib
from importlib.metadata import PackageNotFoundError, version
import io
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from time import perf_counter_ns, thread_time_ns
from typing import TYPE_CHECKING, Any
import zlib


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType, ModuleType


_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))


@dataclass(frozen=True)
class _Case:
    name: str
    raw: bytes
    packed: bytes
    operation: str = "decompress"
    limit: int = 0
    fixture: str | None = None
    chunk_bytes: int = 64 * 1024


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _cases(fixtures: list[Path]) -> list[_Case]:
    def encoded(value: object) -> bytes:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()

    cases = [
        _Case(path.name, raw, gzip.compress(raw, mtime=0), fixture=str(path))
        for path in fixtures
        for raw in [path.read_bytes()]
    ]
    for count, titles in ((100, False), (20001, False), (40001, True)):
        rows = [
            {"id": i, **({"title": f"private-source-canary-{i}"} if titles else {})}
            for i in range(count)
        ]
        raw = encoded(rows)
        packed = gzip.compress(raw, mtime=0)
        cases.append(_Case(f"dataset-{count}-decompress", raw, packed))
        cases.append(_Case(f"dataset-{count}-stream", raw, packed, "stream"))
        cases.append(_Case(f"upload-{count}-two-passes", raw, packed, "upload"))
    raw = b" " * 1_000_000
    packed = gzip.compress(raw, mtime=0)
    cases.append(
        _Case("http-bounded-expansion", raw, packed, "bounded", len(packed) * 2)
    )
    for count in (200, 20000):
        lines = [
            encoded(
                {
                    "s": i,
                    "w": float(i),
                    "m": float(i),
                    "k": "tick",
                    "p": {"dt": 1.0, "energy": (i % 100) / 100},
                    "h": i,
                }
            )
            + b"\n"
            for i in range(count)
        ]
        raw = b"".join(lines)
        packed = b"".join(
            gzip.compress(b"".join(lines[i : i + 100]), mtime=0)
            for i in range(0, count, 100)
        )
        cases.append(_Case(f"journal-{count}-members", raw, packed, "lines"))
    for period in (1, 16, 32):
        raw = (bytes(range(period)) * ((1024 * 1024 + period - 1) // period))[
            : 1024 * 1024
        ]
        packed = gzip.compress(raw, mtime=0)
        cases.extend(
            _Case(
                f"period-{period}-stream-{chunk_bytes}",
                raw,
                packed,
                "stream-count",
                chunk_bytes=chunk_bytes,
            )
            for chunk_bytes in (8192, 64 * 1024, 256 * 1024)
        )
    return cases


def _operation(codec: ModuleType, case: _Case) -> Callable[[], object]:
    if case.operation == "decompress":
        return lambda: codec.decompress(case.packed)

    if case.operation == "upload":
        from blake3 import blake3

        def upload() -> tuple[bytes, int, int]:
            with codec.GzipFile(fileobj=io.BytesIO(case.packed), mode="rb") as source:
                digest, measured = blake3(), 0
                while chunk := source.read(256 * 1024):
                    digest.update(chunk)
                    measured += len(chunk)
                source.seek(0)
                streamed = 0
                while chunk := source.read(256 * 1024):
                    streamed += len(chunk)
                return digest.digest(), measured, streamed

        return upload

    def consume() -> object:
        with codec.GzipFile(fileobj=io.BytesIO(case.packed), mode="rb") as source:
            if case.operation == "bounded":
                return source.read(case.limit + 1)
            if case.operation == "lines":
                return b"".join(source)
            if case.operation == "stream":
                return b"".join(iter(lambda: source.read(64 * 1024), b""))
            if case.operation == "stream-count":
                count = 0
                while chunk := source.read(case.chunk_bytes):
                    count += len(chunk)
                return count
            message = f"unknown gzip benchmark operation: {case.operation}"
            raise ValueError(message)

    return consume


def _validate_stream_bytes(codec: ModuleType, case: _Case) -> None:
    """A length-only timer still needs independent byte checks at its read size."""
    with codec.GzipFile(fileobj=io.BytesIO(case.packed), mode="rb") as source:
        position = 0
        while chunk := source.read(case.chunk_bytes):
            if chunk != case.raw[position : position + len(chunk)]:
                message = f"{case.name}: decoded bytes differ from the source"
                raise AssertionError(message)
            position += len(chunk)
        if position != len(case.raw):
            message = f"{case.name}: decoded bytes differ from the source"
            raise AssertionError(message)


def _native_backends(
    codec: ModuleType, function: Callable[[], object]
) -> dict[str, int] | None:
    """Observe selected engines outside timing, retaining each completed state."""
    if codec is gzip:
        return None
    reader = sys.modules.get("unstd.compress.engines.reader") or sys.modules.get(
        "unstd.compress._reader"
    )
    factory = getattr(reader, "_factory", None)
    if not callable(factory) or not hasattr(factory, "_selected_backend"):
        return None
    instances: list[Any] = []

    def observe(*args: Any, **kwargs: Any) -> Any:
        stream = factory(*args, **kwargs)
        instances.append(stream)
        return stream

    assert reader is not None
    vars(reader)["_factory"] = observe
    try:
        function()
    finally:
        vars(reader)["_factory"] = factory
    selected: dict[str, int] = {}
    for stream in instances:
        name = str(stream._selected_backend)
        selected[name] = selected.get(name, 0) + 1
    return selected


def _validate_upload_bytes(codec: ModuleType, case: _Case) -> None:
    """Check both passes against source bytes outside the timed operation."""
    with codec.GzipFile(fileobj=io.BytesIO(case.packed), mode="rb") as source:
        for _pass in range(2):
            position = 0
            while chunk := source.read(256 * 1024):
                if chunk != case.raw[position : position + len(chunk)]:
                    message = f"{case.name}: decoded bytes differ from the source"
                    raise AssertionError(message)
                position += len(chunk)
            if position != len(case.raw):
                message = f"{case.name}: decoded bytes differ from the source"
                raise AssertionError(message)
            source.seek(0)


def _native_calls(function: Callable[[], object]) -> dict[str, int]:
    calls: dict[str, int] = {}

    def record(_frame: FrameType, event: str, value: Any) -> None:
        if event == "c_call":
            owner = getattr(value, "__self__", None)
            module = getattr(value, "__module__", None) or type(owner).__module__
            if module == "zlib" or module.startswith(
                ("zlib_ng", "unstd.compress._gzip_native")
            ):
                name = f"{module}.{value.__name__}"
                calls[name] = calls.get(name, 0) + 1

    previous = sys.getprofile()
    sys.setprofile(record)
    try:
        function()
    finally:
        sys.setprofile(previous)
    return calls


def _metadata(codec: ModuleType, cases: list[_Case]) -> dict[str, object]:
    versions: dict[str, str | None] = {}
    for package in ("unstd", "zlib-ng", "blake3"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    assert codec.__file__ is not None
    paths = list(
        dict.fromkeys((Path(codec.__file__).resolve(), Path(gzip.__file__).resolve()))
    )
    kernels: dict[str, str] = {}
    streaming: dict[str, object] = {}
    if codec is not gzip:
        paths.extend(
            path
            for name in ("_inflate.py", "_reader.py", "_backend.py")
            if (path := paths[0].with_name(name)).exists()
        )
        paths.extend(sorted((paths[0].parent / "engines").glob("*.py")))
        native = sys.modules.get("unstd.compress._gzip_native")
        if native is not None and native.__file__ is not None:
            paths.append(Path(native.__file__).resolve())
            kernels = {
                name: getattr(native, name)
                for name in ("ZLIB_RUNTIME_VERSION", "LIBDEFLATE_VERSION")
            }
        for name in ("zlib_ng.gzip_ng", "zlib_ng.zlib_ng"):
            backend = sys.modules.get(name)
            if backend is not None and backend.__file__ is not None:
                paths.append(Path(backend.__file__).resolve())
        reader = sys.modules.get("unstd.compress.engines.reader") or sys.modules.get(
            "unstd.compress._reader"
        )
        if reader is not None:
            factory = getattr(reader, "_factory", None)
            owner = getattr(factory, "__module__", "")
            streaming = {
                "factory": f"{owner}.{getattr(factory, '__qualname__', '')}",
                "runtime_version": (
                    getattr(
                        sys.modules.get("zlib_ng.zlib_ng"),
                        "ZLIBNG_RUNTIME_VERSION",
                        None,
                    )
                    if owner.startswith("zlib_ng")
                    else kernels.get("ZLIB_RUNTIME_VERSION")
                ),
                "crc32": getattr(getattr(reader, "_crc32", None), "__module__", None),
                "adaptive_ng_version": getattr(reader, "_adaptive_ng_version", None),
                "isal_runtime_version": getattr(native, "ISAL_RUNTIME_VERSION", None),
                "isal_patch_sha256": getattr(native, "ISAL_PATCH_SHA256", None),
                "isal_streaming": bool(getattr(native, "ISAL_STREAMING", False)),
            }
    packed = codec.compress(cases[0].raw, mtime=0)
    if gzip.decompress(packed) != cases[0].raw:
        message = "compression changed the source bytes"
        raise AssertionError(message)
    native_calls = _native_calls(lambda: codec.compress(cases[0].raw, mtime=0))
    if getattr(codec, "_backend", None) is not None and not any(
        name.startswith(("zlib_ng", "unstd.compress._gzip_native"))
        for name in native_calls
    ):
        message = "installed native gzip backend did not execute compression"
        raise AssertionError(message)
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "zlib_version": zlib.ZLIB_VERSION,
        "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
        "versions": versions,
        "native_kernel_versions": kernels,
        "streaming_codec": streaming,
        "sources": {str(path): _hash(path.read_bytes()) for path in paths},
        "compression_native_calls": native_calls,
        "compression_output_sha256": _hash(packed),
    }


def _worker(args: argparse.Namespace) -> None:
    source = None
    if args.source:
        source = args.source.resolve()
        source = source / "src" if (source / "src/unstd").exists() else source
        sys.path.insert(0, str(source))
    codec = gzip if args.stdlib else importlib.import_module("unstd.compress.gzipx")
    assert codec.__file__ is not None
    if (
        source is not None
        and Path(codec.__file__).resolve() != source / "unstd/compress/gzipx.py"
    ):
        message = f"requested source was not imported: {source}"
        raise RuntimeError(message)
    from bench.core.timing import measure

    cases = _cases(args.fixture)
    functions = [_operation(codec, case) for case in cases]
    evidence = []
    for case, function in zip(cases, functions, strict=True):
        actual = function()
        expected = (
            case.raw[: case.limit + 1] if case.operation == "bounded" else case.raw
        )
        upload_summary = None
        if case.operation == "upload":
            from blake3 import blake3

            digest = blake3(case.raw).digest()
            upload_summary = digest, len(case.raw), len(case.raw)
            identical = actual == upload_summary
            _validate_upload_bytes(codec, case)
        elif case.operation == "stream-count":
            identical = actual == len(case.raw)
            _validate_stream_bytes(codec, case)
        else:
            identical = actual == expected
        if not identical:
            message = f"{case.name}: decoded bytes differ from the source"
            raise AssertionError(message)
        calls = _native_calls(function)
        entry: dict[str, Any] = {
            "name": case.name,
            "operation": case.operation,
            "fixture": case.fixture,
            "raw_bytes": len(case.raw),
            "compressed_bytes": len(case.packed),
            "raw_sha256": _hash(case.raw),
            "compressed_sha256": _hash(case.packed),
            "output_bytes": len(expected),
            "output_sha256": _hash(expected),
            "identical_output": True,
            "native_calls": calls,
            "selected_native_backends": _native_backends(codec, function),
            "read_chunk_bytes": case.chunk_bytes
            if case.operation == "stream-count"
            else None,
            "native_decode_executed": any(
                name.startswith(("zlib_ng", "unstd.compress._gzip_native"))
                and "decompress" in name
                for name in calls
            ),
        }
        if upload_summary is not None:
            entry["upload_summary"] = {
                "blake3": upload_summary[0].hex(),
                "measured_bytes": upload_summary[1],
                "streamed_bytes": upload_summary[2],
            }
        native_buffer = getattr(codec, "_decode_buffer", None)
        if case.operation == "decompress" and native_buffer is not None:
            entry["native_buffer_accepts_fixture"] = (
                native_buffer(case.packed) == expected
            )
        evidence.append(entry)
    print(
        json.dumps({"metadata": _metadata(codec, cases), "cases": evidence}), flush=True
    )
    for line in sys.stdin:
        request = json.loads(line)
        if isinstance(request, int):
            result = asdict(measure(functions[request], rounds=2))
        else:
            index, iterations = request["case"], request["iterations"]
            function = functions[index]
            enabled = gc.isenabled()
            gc.disable()
            try:
                wall, cpu = perf_counter_ns(), thread_time_ns()
                for _ in range(iterations):
                    function()
                elapsed_cpu = thread_time_ns() - cpu
                elapsed_wall = perf_counter_ns() - wall
            finally:
                if enabled:
                    gc.enable()
            result = {
                "per_op_ns": elapsed_wall / iterations,
                "cpu_ns": elapsed_cpu / iterations,
                "iters": iterations,
            }
            if cases[index].operation == "stream-count":
                _validate_stream_bytes(codec, cases[index])
        print(json.dumps(result), flush=True)


def _require_native(cases: list[dict[str, Any]]) -> None:
    for case in cases:
        if (
            not case["native_decode_executed"]
            or case.get("native_buffer_accepts_fixture") is False
        ):
            message = f"{case['name']}: accelerated decoder did not accept the workload"
            raise AssertionError(message)


def _main(args: argparse.Namespace) -> None:
    variants = {"stdlib": None, "candidate": args.source or _ROOT / "src"}
    if args.baseline_source:
        variants["baseline"] = args.baseline_source
    processes: dict[str, subprocess.Popen[str]] = {}
    report: dict[str, object] = {
        "schema": 1,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "harness_sha256": _hash(Path(__file__).read_bytes()),
        "require_native": args.require_native,
        "runs": args.runs,
        "variants": {},
        "method": {
            "clock": "thread_time_ns CPU and perf_counter_ns wall",
            "minimum_batch_cpu_ms": args.batch_ms,
            "iterations": "common across variants; fixed after joint calibration",
            "gc": "disabled within timed batches",
            "startup_and_ipc": "excluded",
            "upload_workload": {
                "revision": "bounded-blake3-rewind-stream-v1",
                "chunk_bytes": 256 * 1024,
                "first_pass": "incremental BLAKE3 digest and byte count",
                "second_pass": "rewind, count bytes and discard chunks",
                "timed_result": "32-byte digest and both byte counts",
                "byte_validation": "both passes checked against source before timing",
            },
        },
    }
    records: dict[str, dict[str, Any]] = {}
    try:
        for name, source in variants.items():
            command = [sys.executable, str(Path(__file__).resolve()), "--worker"]
            command += ["--source", str(source.resolve())] if source else ["--stdlib"]
            for fixture in args.fixture:
                command += ["--fixture", str(fixture)]
            child = subprocess.Popen(  # noqa: S603 — this script and explicit local source paths.
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
            )
            processes[name] = child
            assert child.stdout is not None
            line = child.stdout.readline()
            if not line:
                message = f"{name} worker failed before publishing its fixtures"
                raise RuntimeError(message)
            records[name] = json.loads(line)
            for case in records[name]["cases"]:
                case["measurements"] = []
        reference = records["stdlib"]["cases"]
        for record in records.values():
            for actual, expected in zip(record["cases"], reference, strict=True):
                for field in (
                    "name",
                    "raw_sha256",
                    "compressed_sha256",
                    "output_sha256",
                ):
                    if actual[field] != expected[field]:
                        message = f"variant fixture or output mismatch: {field}"
                        raise AssertionError(message)
        if args.require_native:
            _require_native(records["candidate"]["cases"])

        def sample(name: str, index: int, iterations: int) -> dict[str, Any]:
            child = processes[name]
            assert child.stdin is not None
            assert child.stdout is not None
            child.stdin.write(
                json.dumps({"case": index, "iterations": iterations}) + "\n"
            )
            child.stdin.flush()
            response = child.stdout.readline()
            if not response:
                message = f"{name} worker stopped while measuring case {index}"
                raise RuntimeError(message)
            return dict(json.loads(response))

        order = list(variants)
        report["sample_order"] = [
            order if run % 2 == 0 else order[::-1] for run in range(args.runs)
        ]
        for index in range(len(reference)):
            iterations = 1
            calibration = []
            while True:
                pilot = {name: sample(name, index, iterations) for name in order}
                calibration.append({"iterations": iterations, "measurements": pilot})
                if (
                    min(value["cpu_ns"] for value in pilot.values()) * iterations
                    >= args.batch_ms * 1_000_000
                ):
                    break
                if iterations >= 1 << 22:
                    message = "batch calibration exceeded the iteration budget"
                    raise RuntimeError(message)
                iterations *= 2
            for record in records.values():
                record["cases"][index]["iterations"] = iterations
                record["cases"][index]["calibration"] = calibration
            for run in range(args.runs):
                for name in order if run % 2 == 0 else order[::-1]:
                    records[name]["cases"][index]["measurements"].append(
                        sample(name, index, iterations)
                    )
        for record in records.values():
            for case in record["cases"]:
                case["median_ns"] = statistics.median(
                    item["per_op_ns"] for item in case["measurements"]
                )
                case["median_cpu_ns"] = statistics.median(
                    item["cpu_ns"] for item in case["measurements"]
                )
        report["variants"] = records
        output = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output)
        else:
            print(output, end="")
        for index, case in enumerate(reference):
            costs = ", ".join(
                f"{name} {record['cases'][index]['median_ns'] / 1000:.1f} us"
                for name, record in records.items()
            )
            print(f"{case['name']}: {costs}", file=sys.stderr)
    finally:
        for child in processes.values():
            if child.stdin:
                child.stdin.close()
            if child.poll() is None:
                child.terminate()
            child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--fixture", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--batch-ms", type=float, default=20)
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stdlib", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    arguments.fixture = [path.resolve() for path in arguments.fixture]
    if arguments.runs < 1:
        parser.error("--runs must be positive")
    if arguments.batch_ms <= 0:
        parser.error("--batch-ms must be positive")
    (_worker if arguments.worker else _main)(arguments)
