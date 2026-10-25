"""Paired C gzip encodes from requests and dataset materialization.

Run this file directly so workers select an installation before importing unstd.
The separate frozen fixture leaves the existing 14 stream cases unchanged.
"""

from __future__ import annotations

import argparse
import ast
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
import gc
import gzip
import hashlib
import importlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
from threading import Lock
from time import perf_counter_ns, thread_time_ns
from typing import TYPE_CHECKING, Literal, Protocol, cast
import zlib


if TYPE_CHECKING:
    from collections.abc import Buffer, Callable


_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = "services/ai/core/foundation/util/text/compress.py"
_DATASETS = "services/ai/tools/base/datasets.py"
_WINDOW = 1 << 16
_THRESHOLD = 1024


class _Compressor(Protocol):
    def compress(self, data: Buffer, /) -> bytes: ...
    def copy(self) -> _Compressor: ...
    def flush(self) -> bytes: ...


class _Codec(Protocol):
    def compress(self, data: Buffer, /, level: int = -1, wbits: int = 15) -> bytes: ...
    def compressobj(self, *, level: int, wbits: int) -> _Compressor: ...


class _GzipSurface(Protocol):
    def compress(self, data: Buffer, compresslevel: int, *, mtime: int) -> bytes: ...


class _GzipCodec:
    """Express the one-shot gzip wire operation through the gzip API."""

    def __init__(self, module: _GzipSurface) -> None:
        self._module = module

    def compress(self, data: Buffer, /, level: int = -1, wbits: int = 15) -> bytes:
        if wbits != 31:
            message = "the gzip request adapter requires the gzip window mode"
            raise ValueError(message)
        return self._module.compress(
            data, compresslevel=6 if level == -1 else level, mtime=0
        )

    def compressobj(self, *, level: int, wbits: int) -> _Compressor:
        message = "gzip request mode supports direct encodes only"
        raise NotImplementedError(message)


@dataclass(frozen=True)
class Case:
    """One direct, fresh-scope or warmed checkpoint request sequence."""

    name: str
    mode: Literal["direct", "scope", "warm", "materialize"]
    requests: tuple[tuple[bytes, int], ...]


class _Prefix:
    """64 KiB immutable compressor checkpoint, with an injected codec."""

    __slots__ = ("_closed", "_codec", "_lock", "_state")

    def __init__(self, codec: _Codec) -> None:
        self._codec = codec
        self._lock = Lock()
        self._closed = False
        self._state: tuple[int, bytes, bytes, _Compressor | None] | None = None

    def encode(self, data: bytes, level: int) -> bytes | None:
        if len(data) < _WINDOW:
            return None
        with self._lock:
            if self._closed:
                return None
            state = self._state
            if state is None:
                self._state = level, data[:_WINDOW], b"", None
                return None
            if state[0] != level or not data.startswith(state[1]):
                return None
            _, prefix, head, encoder = state
            cloned = encoder.copy() if encoder is not None else None
        if cloned is None:
            encoder = self._codec.compressobj(level=level, wbits=31)
            head = encoder.compress(prefix)
            with self._lock:
                if self._state is state:
                    self._state = level, prefix, head, encoder
                cloned = encoder.copy()
        return head + cloned.compress(memoryview(data)[len(prefix) :]) + cloned.flush()

    def close(self) -> None:
        with self._lock:
            self._closed, self._state = True, None


def _request(codec: _Codec, data: bytes, level: int, owner: _Prefix | None) -> bytes:
    if len(data) < _THRESHOLD:
        return data
    packed = owner.encode(data, level) if owner is not None else None
    if packed is None:
        packed = codec.compress(data, level=level, wbits=31)
    return packed if len(packed) < len(data) else data


def _operation(codec: _Codec, case: Case) -> Callable[[], tuple[bytes | str, ...]]:
    if case.mode == "materialize":
        from unstd.serde import b64, jsonx

        rows = [(json.loads(data), level) for data, level in case.requests]

        def materialize() -> tuple[str, ...]:
            return tuple(
                b64.encode(
                    codec.compress(
                        jsonx.dumps(
                            values, ensure_ascii=False, separators=(",", ":")
                        ).encode(),
                        level=level,
                        wbits=31,
                    )
                )
                for values, level in rows
            )

        return materialize
    owner = _Prefix(codec) if case.mode == "warm" else None
    if owner is not None:
        first, level = case.requests[0]
        assert owner.encode(first, level) is None
        assert owner.encode(first, level) is not None

    def operation() -> tuple[bytes, ...]:
        current = _Prefix(codec) if case.mode == "scope" else owner
        try:
            return tuple(
                _request(codec, data, level, current) for data, level in case.requests
            )
        finally:
            if current is not None and case.mode == "scope":
                current.close()

    return operation


def _validate(codec: _Codec, case: Case, result: tuple[bytes | str, ...]) -> None:
    if case.mode == "materialize":
        materialized = tuple(
            base64.b64encode(codec.compress(data, level=level, wbits=31)).decode()
            for data, level in case.requests
        )
        if result != materialized:
            message = f"{case.name}: materialized content changed"
            raise AssertionError(message)
        for value, (raw, _) in zip(result, case.requests, strict=True):
            if gzip.decompress(base64.b64decode(value, validate=True)) != raw:
                message = f"{case.name}: materialized rows changed"
                raise AssertionError(message)
        return
    expected = tuple(
        _request(codec, data, level, None) for data, level in case.requests
    )
    if result != expected:
        message = f"{case.name}: checkpoint compressed bytes changed"
        raise AssertionError(message)
    for packed, (raw, _) in zip(result, case.requests, strict=True):
        assert isinstance(packed, bytes)
        decoded = gzip.decompress(packed) if packed.startswith(b"\x1f\x8b") else packed
        if decoded != raw:
            message = f"{case.name}: recovered bytes changed"
            raise AssertionError(message)


def _body(size: int, revision: int = 0, *, changed_prefix: bool = False) -> bytes:
    rng = random.Random(6419)
    rows = [
        {
            "role": "user" if index % 2 else "assistant",
            "content": f"Public fixture {index}: "
            + " ".join(str(rng.randrange(1_000_000)) for _ in range(36)),
        }
        for index in range(max(1, size // 300))
    ]
    return json.dumps(
        {
            "model": "benchmark-model",
            "tools": [
                {
                    "type": "function",
                    "name": f"public_{index}",
                    "description": "Public request compression fixture",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                }
                for index in range(16)
            ],
            "prefix_variant": int(changed_prefix),
            "messages": rows,
            "revision": revision,
        },
        separators=(",", ":"),
    ).encode()


def _cases() -> list[Case]:
    cases = []
    for size in (4 * 1024, 128 * 1024, 512 * 1024):
        body = _body(size)
        cases.append(
            Case(f"request-direct-{size // 1024}k-level6", "direct", ((body, 6),))
        )
    for size in (128 * 1024, 512 * 1024):
        requests = tuple((_body(size, revision), 6) for revision in range(4))
        modes: tuple[Literal["scope", "warm"], ...] = ("scope", "warm")
        cases.extend(
            Case(
                f"request-prefix-{mode}-{size // 1024}k-level6",
                mode,
                requests,
            )
            for mode in modes
        )
    body = _body(128 * 1024)
    cases.append(
        Case(
            "request-prefix-level-and-prefix-misses",
            "scope",
            (
                (body, 6),
                (body, 6),
                (body, 1),
                (_body(128 * 1024, changed_prefix=True), 6),
                (body, 9),
                (body, 6),
            ),
        )
    )
    source = json.loads(
        gzip.decompress((_ROOT / "bench/workloads/billy_gzip.json.gz").read_bytes())
    )
    for item in source["cases"]:
        if item["name"].startswith("dataset-query-"):
            raw = base64.b64decode(item["raw"], validate=True)
            assert _hash(raw) == item["raw_sha256"]
            cases.append(
                Case(
                    item["name"].replace("query", "materialize"),
                    "materialize",
                    ((raw, 9),),
                )
            )
    return cases


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixtures(billy: Path, output: Path) -> None:
    path = billy / _SOURCE
    raw = path.read_bytes()
    source = raw.decode()
    nodes = {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef | ast.FunctionDef)
    }
    receipts = {}
    for name in ("gzip_encode", "gzip_request", "_GzipPrefix"):
        node = nodes[name]
        body = ast.get_source_segment(source, node)
        assert body is not None
        receipts[name] = {
            "path": _SOURCE,
            "symbol": name,
            "line": node.lineno,
            "file_sha256": _hash(raw),
            "symbol_sha256": _hash(body.encode()),
        }
    dataset_bytes = (billy / _DATASETS).read_bytes()
    dataset_source = dataset_bytes.decode()
    dataset_node = next(
        node
        for node in ast.parse(dataset_source).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "query_dataset"
    )
    body = ast.get_source_segment(dataset_source, dataset_node)
    assert body is not None
    receipts["dataset_materialize_encode"] = {
        "path": _DATASETS,
        "symbol": dataset_node.name,
        "line": dataset_node.lineno,
        "file_sha256": _hash(dataset_bytes),
        "symbol_sha256": _hash(body.encode()),
    }
    chunks = {}
    bodies = {}
    cases = _cases()
    for case in cases:
        for data, _ in case.requests:
            pieces = []
            for start in range(0, len(data), _WINDOW):
                piece = data[start : start + _WINDOW]
                digest = _hash(piece)
                chunks[digest] = base64.b64encode(piece).decode()
                pieces.append(digest)
            bodies[_hash(data)] = pieces
    document = {
        "schema": 1,
        "source_contracts": receipts,
        "threshold": _THRESHOLD,
        "window": _WINDOW,
        "chunks": chunks,
        "bodies": bodies,
        "cases": [
            {
                "name": case.name,
                "mode": case.mode,
                "requests": [
                    {
                        "level": level,
                        "sha256": _hash(data),
                    }
                    for data, level in case.requests
                ],
            }
            for case in cases
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        gzip.compress(
            (json.dumps(document, separators=(",", ":")) + "\n").encode(), mtime=0
        )
    )


def _load(path: Path) -> tuple[list[Case], dict[str, object]]:
    document = json.loads(gzip.decompress(path.read_bytes()))
    if (
        document["schema"] != 1
        or document["threshold"] != _THRESHOLD
        or document["window"] != _WINDOW
    ):
        message = "unsupported Billy request fixture contract"
        raise ValueError(message)
    chunks = {}
    for digest, encoded in document["chunks"].items():
        piece = base64.b64decode(encoded, validate=True)
        if _hash(piece) != digest:
            message = "request fixture chunk bytes changed"
            raise ValueError(message)
        chunks[digest] = piece
    bodies = {}
    for digest, pieces in document["bodies"].items():
        body = b"".join(chunks[piece] for piece in pieces)
        if _hash(body) != digest:
            message = "request fixture bytes changed"
            raise ValueError(message)
        bodies[digest] = body
    cases = []
    for value in document["cases"]:
        requests = []
        for item in value["requests"]:
            body = bodies[item["sha256"]]
            requests.append((body, item["level"]))
        cases.append(Case(value["name"], value["mode"], tuple(requests)))
    return cases, document["source_contracts"]


def _worker(args: argparse.Namespace) -> None:
    source = args.source.resolve()
    source = source / "src" if (source / "src/unstd").exists() else source
    sys.path.insert(0, str(source))
    namespace = getattr(args, "codec", "zlibx")
    module = (
        zlib if args.stdlib else importlib.import_module(f"unstd.compress.{namespace}")
    )
    if (
        not args.stdlib
        and Path(cast("str", module.__file__)).resolve()
        != source / f"unstd/compress/{namespace}.py"
    ):
        message = "requested zlib source was not imported"
        raise RuntimeError(message)
    codec: _Codec = (
        _GzipCodec(cast("_GzipSurface", module))
        if namespace == "gzipx" and not args.stdlib
        else cast("_Codec", module)
    )
    cases, receipts = _load(args.fixtures)
    if namespace == "gzipx":
        cases = [case for case in cases if case.mode in {"direct", "materialize"}]
    if args.case:
        cases = [
            case
            for case in cases
            if any(fnmatchcase(case.name, pattern) for pattern in args.case)
        ]
    if not cases:
        message = "no request cases selected"
        raise ValueError(message)
    functions = [_operation(codec, case) for case in cases]
    failures = {}
    for case, function in zip(cases, functions, strict=True):
        try:
            _validate(codec, case, function())
        except AssertionError as error:
            failures[case.name] = str(error)
    # Import after source selection: bench's package initializer imports unstd.
    # File-based invocation puts only this profile directory on sys.path.
    sys.path.append(str(_ROOT))
    from bench.profiles import gzip_reads

    native = [gzip_reads._native_calls(function) for function in functions]
    if args.require_native and any(
        not any(
            name.startswith(("zlib_ng", "unstd.compress._gzip_native"))
            for name in calls
        )
        for case, calls in zip(cases, native, strict=True)
        if case.name not in failures
    ):
        message = "request compression native kernel was not exercised"
        raise RuntimeError(message)
    provider = sys.modules.get("zlib_ng.zlib_ng")
    provider_path = getattr(provider, "__file__", None)
    kernel = sys.modules.get("unstd.compress._gzip_native")
    kernel_path = getattr(kernel, "__file__", None)
    print(
        json.dumps(
            {
                "source": str(source),
                "python": sys.version,
                "codec": namespace,
                "source_contracts": receipts,
                "module_sha256": _hash(Path(cast("str", module.__file__)).read_bytes()),
                "provider": {
                    "path": provider_path,
                    "sha256": _hash(Path(provider_path).read_bytes())
                    if isinstance(provider_path, str)
                    else None,
                    "version": getattr(provider, "ZLIBNG_RUNTIME_VERSION", None),
                },
                "native_kernel": {
                    "path": kernel_path,
                    "sha256": _hash(Path(kernel_path).read_bytes())
                    if isinstance(kernel_path, str)
                    else None,
                    "libdeflate_version": getattr(kernel, "LIBDEFLATE_VERSION", None),
                },
                "cases": [
                    {
                        "name": case.name,
                        "operation": case.mode,
                        "raw_bytes": sum(len(data) for data, _ in case.requests),
                        "raw_sha256": [_hash(data) for data, _ in case.requests],
                        "levels": [level for _, level in case.requests],
                        "native_calls": calls,
                        "packed_bytes": [len(value) for value in function()],
                        "eligible": case.name not in failures,
                        "contract_failure": failures.get(case.name),
                    }
                    for case, calls, function in zip(
                        cases, native, functions, strict=True
                    )
                ],
            }
        ),
        flush=True,
    )
    for line in sys.stdin:
        request = json.loads(line)
        index, iterations = request["case"], request["iterations"]
        function = functions[index]
        assert cases[index].name not in failures
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            wall, cpu = perf_counter_ns(), thread_time_ns()
            for _ in range(iterations):
                function()
            cpu, wall = thread_time_ns() - cpu, perf_counter_ns() - wall
        finally:
            if was_enabled:
                gc.enable()
        _validate(codec, cases[index], function())
        print(
            json.dumps(
                {
                    "cpu_ns": cpu / iterations,
                    "wall_ns": wall / iterations,
                    "iterations": iterations,
                }
            ),
            flush=True,
        )


def _run(args: argparse.Namespace) -> None:
    sources = {"candidate": args.source}
    if args.baseline_source is not None:
        sources["baseline"] = args.baseline_source
    children: dict[str, subprocess.Popen[str]] = {}
    records = {}
    report: dict[str, object] = {
        "schema": 1,
        "codec": getattr(args, "codec", "zlibx"),
        "recorded_utc": datetime.now(UTC).isoformat(),
        "harness_sha256": _hash(Path(__file__).read_bytes()),
        "fixtures_sha256": _hash(args.fixtures.read_bytes()),
        "rounds": args.rounds,
        "minimum_batch_cpu_ms": args.batch_ms,
        "method": "persistent workers, alternating per-case batches, common calibrated iterations, validation outside timing",
    }
    try:
        for name, source in sources.items():
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "worker",
                "--source",
                str(source),
                "--fixtures",
                str(args.fixtures),
                "--codec",
                getattr(args, "codec", "zlibx"),
            ]
            if args.require_native:
                command.append("--require-native")
            for pattern in args.case:
                command.extend(("--case", pattern))
            child = subprocess.Popen(  # noqa: S603 — fixed worker and explicit local installations
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
            )
            children[name] = child
            assert child.stdout is not None
            line = child.stdout.readline()
            if not line:
                message = f"{name} worker failed before publishing its fixtures"
                raise RuntimeError(message)
            records[name] = json.loads(line)
        reference = records["candidate"]["cases"]
        for record in records.values():
            assert record["cases"]
            assert len(record["cases"]) == len(reference)
            for actual, expected in zip(record["cases"], reference, strict=True):
                for key in ("name", "raw_sha256", "levels"):
                    assert actual[key] == expected[key], key

        def sample(name: str, index: int, iterations: int) -> dict[str, float]:
            child = children[name]
            assert child.stdin is not None
            assert child.stdout is not None
            child.stdin.write(
                json.dumps({"case": index, "iterations": iterations}) + "\n"
            )
            child.stdin.flush()
            response = child.stdout.readline()
            if not response:
                message = f"{name} worker stopped at request case {index}"
                raise RuntimeError(message)
            return cast("dict[str, float]", json.loads(response))

        rows = []
        for index, case in enumerate(reference):
            failures = {
                name: record["cases"][index]["contract_failure"]
                for name, record in records.items()
                if not record["cases"][index]["eligible"]
            }
            if failures:
                rows.append(
                    {
                        "name": case["name"],
                        "eligible": False,
                        "contract_failures": failures,
                    }
                )
                continue
            iterations = 1
            calibration = []
            while True:
                pilot = {name: sample(name, index, iterations) for name in children}
                calibration.append(pilot)
                if (
                    min(value["cpu_ns"] * iterations for value in pilot.values())
                    >= args.batch_ms * 1e6
                ):
                    break
                iterations *= 2
                if iterations > 1 << 20:
                    message = "request calibration exceeded iteration budget"
                    raise RuntimeError(message)
            samples: dict[str, list[dict[str, float]]] = {name: [] for name in children}
            for run in range(args.rounds):
                order = list(children)
                for name in order if run % 2 == 0 else order[::-1]:
                    samples[name].append(sample(name, index, iterations))
            row = {
                "name": case["name"],
                "eligible": True,
                "raw_bytes": case["raw_bytes"],
                "samples": samples,
                "calibration": calibration,
            }
            for clock in ("cpu", "wall"):
                if "baseline" in samples:
                    row[f"speedup_{clock}"] = statistics.median(
                        before[f"{clock}_ns"] / after[f"{clock}_ns"]
                        for before, after in zip(
                            samples["baseline"], samples["candidate"], strict=True
                        )
                    )
            rows.append(row)
        report["records"], report["cases"] = records, rows
        report["regressions"] = [
            row["name"]
            for row in rows
            if args.max_slowdown is not None
            and row["eligible"]
            and float(cast("float", row["speedup_wall"])) < 1 / args.max_slowdown
        ]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        failed_cases = [row["name"] for row in rows if not row["eligible"]]
        if failed_cases:
            message = f"Billy request contract failures: {failed_cases}"
            raise RuntimeError(message)
        if report["regressions"]:
            message = f"Billy request latency regressions: {report['regressions']}"
            raise RuntimeError(message)
    finally:
        for child in children.values():
            if child.stdin is not None:
                child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main() -> None:
    """Select frozen fixture capture, isolated worker or paired timing."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fixture = commands.add_parser("fixtures")
    fixture.add_argument("--billy-source", type=Path, required=True)
    fixture.add_argument("--output", type=Path, required=True)
    for name in ("worker", "run"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--fixtures", type=Path, required=True)
        command.add_argument("--require-native", action="store_true")
        command.add_argument("--case", action="append", default=[])
        command.add_argument(
            "--codec",
            choices=("zlibx", "gzipx"),
            default="zlibx",
            help="gzipx measures whole-buffer encodes; checkpoints use zlibx",
        )
        if name == "worker":
            command.add_argument("--stdlib", action="store_true")
        else:
            command.add_argument("--baseline-source", type=Path)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--rounds", type=int, default=9)
            command.add_argument("--batch-ms", type=float, default=20)
            command.add_argument("--max-slowdown", type=float)
    args = parser.parse_args()
    if args.command == "fixtures":
        _fixtures(args.billy_source, args.output)
    elif args.command == "worker":
        _worker(args)
    elif (
        args.rounds < 2
        or args.batch_ms <= 0
        or (
            args.max_slowdown is not None
            and (args.baseline_source is None or args.max_slowdown <= 0)
        )
    ):
        parser.error(
            "positive batch time and slowdown, at least two rounds, and a comparison for a slowdown guard are required"
        )
    else:
        _run(args)


if __name__ == "__main__":
    main()
