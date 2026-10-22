"""Paired gzip read workloads, including Billy's streaming consumption shapes.

Run ``python bench/gzip_reads.py --baseline-source /saved/src --output reads.json``.
Repeat ``--fixture`` for captured payloads; their original bytes are never rewritten.
Each variant lives in its own process; timing excludes process startup and IPC.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
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
from typing import TYPE_CHECKING, Any
import zlib


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType, ModuleType


_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


@dataclass(frozen=True)
class _Case:
    name: str
    raw: bytes
    packed: bytes
    operation: str = "decompress"
    limit: int = 0
    fixture: str | None = None


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
    return cases


def _operation(codec: ModuleType, case: _Case) -> Callable[[], object]:
    if case.operation == "decompress":
        return lambda: codec.decompress(case.packed)

    def consume() -> object:
        with codec.GzipFile(fileobj=io.BytesIO(case.packed), mode="rb") as source:
            if case.operation == "bounded":
                return source.read(case.limit + 1)
            if case.operation == "lines":
                return b"".join(source)
            if case.operation == "stream":
                return b"".join(iter(lambda: source.read(64 * 1024), b""))
            first = b"".join(iter(lambda: source.read(256 * 1024), b""))
            source.seek(0)
            second = b"".join(iter(lambda: source.read(256 * 1024), b""))
            if first != second:
                message = "gzip rewind changed the uploaded bytes"
                raise AssertionError(message)
            return second

    return consume


def _native_calls(function: Callable[[], object]) -> dict[str, int]:
    calls: dict[str, int] = {}

    def record(_frame: FrameType, event: str, value: Any) -> None:
        if event == "c_call":
            owner = getattr(value, "__self__", None)
            module = getattr(value, "__module__", None) or type(owner).__module__
            if module == "zlib" or module.startswith("zlib_ng"):
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
    for package in ("unstd", "zlib-ng"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    assert codec.__file__ is not None
    paths = [Path(codec.__file__).resolve()]
    if codec is not gzip:
        paths.extend(
            path
            for name in ("_inflate.py", "_backend.py")
            if (path := paths[0].with_name(name)).exists()
        )
    packed = codec.compress(cases[0].raw, mtime=0)
    if gzip.decompress(packed) != cases[0].raw:
        message = "compression changed the source bytes"
        raise AssertionError(message)
    native_calls = _native_calls(lambda: codec.compress(cases[0].raw, mtime=0))
    if getattr(codec, "_backend", None) is not None and not any(
        name.startswith("zlib_ng") for name in native_calls
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
    from bench.timing import measure

    cases = _cases(args.fixture)
    functions = [_operation(codec, case) for case in cases]
    evidence = []
    for case, function in zip(cases, functions, strict=True):
        actual = function()
        expected = (
            case.raw[: case.limit + 1] if case.operation == "bounded" else case.raw
        )
        if actual != expected:
            message = f"{case.name}: decoded bytes differ from the source"
            raise AssertionError(message)
        evidence.append(
            {
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
                "native_calls": _native_calls(function),
            }
        )
    print(
        json.dumps({"metadata": _metadata(codec, cases), "cases": evidence}), flush=True
    )
    for line in sys.stdin:
        index = int(line)
        print(json.dumps(asdict(measure(functions[index], rounds=2))), flush=True)


def _main(args: argparse.Namespace) -> None:
    variants = {"stdlib": None, "candidate": args.source or _ROOT / "src"}
    if args.baseline_source:
        variants["baseline"] = args.baseline_source
    processes: dict[str, subprocess.Popen[str]] = {}
    report: dict[str, object] = {"runs": args.runs, "variants": {}}
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
        order = list(variants)
        report["sample_order"] = [
            order if run % 2 == 0 else order[::-1] for run in range(args.runs)
        ]
        for index in range(len(reference)):
            for run in range(args.runs):
                for name in order if run % 2 == 0 else order[::-1]:
                    child = processes[name]
                    assert child.stdin is not None
                    assert child.stdout is not None
                    child.stdin.write(f"{index}\n")
                    child.stdin.flush()
                    records[name]["cases"][index]["measurements"].append(
                        json.loads(child.stdout.readline())
                    )
        for record in records.values():
            for case in record["cases"]:
                case["median_ns"] = statistics.median(
                    item["per_op_ns"] for item in case["measurements"]
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
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stdlib", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    arguments.fixture = [path.resolve() for path in arguments.fixture]
    if arguments.runs < 1:
        parser.error("--runs must be positive")
    (_worker if arguments.worker else _main)(arguments)
