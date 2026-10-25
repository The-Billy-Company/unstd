"""Checked gzip I/O ports for dataset, HTTP and journal consumers.

Run this file directly. Create transport-identical fixtures,
then use ``run --fixtures FILE --source INSTALL --baseline-source BEFORE --output FILE``.
The timed region includes codec setup, parsing/hashing and close/flush. Async
scheduling, gRPC and filesystem latency are excluded; no decoded-output cache is
used. Fixture receipts retain the upstream consumer source locations and hashes.
The dataset query lane explicitly requires the optional ``ijson`` dependency.
The latency gate uses the median candidate/baseline ratio within matched
repetitions; independent latency medians and every pair remain in the report.
"""

from __future__ import annotations

import argparse
import ast
import base64
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
import gzip
import hashlib
import importlib
from importlib.metadata import version
import io
from itertools import batched
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Protocol

    class _Ijson(Protocol):
        def parse(
            self, source: object, *, use_float: bool
        ) -> Iterator[tuple[str, str, object]]: ...

        def items(
            self, events: Iterator[tuple[str, str, object]], prefix: str
        ) -> Iterator[object]: ...


_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
_CONSUMERS = {
    "query": ("services/ai/tools/base/datasets.py", "query_dataset"),
    "materialize": ("services/ai/tools/base/datasets.py", "materialize_datasets"),
    "upload": (
        "services/ai/tools/code/sandbox/client/channel.py",
        "sandbox_upload_from",
    ),
    "http": ("services/ai/core/foundation/io/net/body.py", "_decoded"),
    "journal_write": ("services/ai/engine/runtime/journal/buffer.py", "_flush_batch"),
    "journal_open": ("services/ai/engine/runtime/journal/buffer.py", "_open_file"),
    "journal_read": ("services/ai/engine/runtime/journal/replay.py", "load_journal"),
    "journal_entry": ("services/ai/engine/runtime/journal/types.py", "JournalEntry"),
    "request_encode": (
        "services/ai/core/foundation/util/text/compress.py",
        "gzip_encode",
    ),
    "request_prefix": (
        "services/ai/core/foundation/util/text/compress.py",
        "_GzipPrefix",
    ),
}


@dataclass(frozen=True)
class Case:
    """A frozen transport fixture and its production I/O contract."""

    name: str
    operation: str
    raw: bytes
    packed: bytes
    budget: int = 0
    batch_rows: int = 100
    members: int = 1


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def _source_contracts(root: Path) -> dict[str, object]:
    receipts: dict[str, object] = {}
    for name, (relative, symbol) in _CONSUMERS.items():
        path = root / relative
        raw = path.read_bytes()
        source = raw.decode()
        matches = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name == symbol
        ]
        if len(matches) != 1:
            message = f"ambiguous or missing Billy consumer: {relative}:{symbol}"
            raise ValueError(message)
        node = matches[0]
        body = ast.get_source_segment(source, node)
        assert body is not None
        receipts[name] = {
            "path": relative,
            "symbol": symbol,
            "line": node.lineno,
            "end_line": node.end_lineno,
            "file_sha256": _sha(raw),
            "symbol_sha256": _sha(body.encode()),
        }
    return receipts


def _journal_bytes(codec: Any, raw: bytes, batch_rows: int, members: int) -> bytes:
    """One write per encoded entry, flush per batch, member per reopen."""
    lines = raw.splitlines(keepends=True)
    if not lines:
        return b""
    target = io.BytesIO()
    # A restart appends another member; ordinary flushes retain one dictionary.
    width = (len(lines) + members - 1) // members
    for member in batched(lines, width):
        with codec.GzipFile(
            filename="journal_0000.jsonl.gz", fileobj=target, mode="ab", mtime=0
        ) as writer:
            for batch in batched(member, batch_rows):
                for line in batch:
                    writer.write(line)
                writer.flush()
    return target.getvalue()


def _cases() -> list[Case]:
    cases: list[Case] = []
    for count in (100, 20001, 40001):
        raw = _json(
            [
                {
                    "id": index,
                    "title": f"café 🎹 source row {index}",
                    "value": index / 10,
                }
                for index in range(count)
            ]
        )
        packed = gzip.compress(raw, mtime=0)
        cases.extend(
            (
                Case(f"dataset-query-{count}", "query", raw, packed),
                Case(f"dataset-upload-{count}", "upload", raw, packed),
            )
        )
    response = _json(
        {
            "items": [
                {"id": index, "text": "source response 東京"} for index in range(2000)
            ]
        }
    )
    cases.append(
        Case(
            "http-bounded-success",
            "bounded",
            response,
            gzip.compress(response, mtime=0),
            len(response),
        )
    )
    bomb = b" " * 1_000_000
    packed = gzip.compress(bomb, mtime=0)
    cases.append(
        Case("http-expansion-refusal", "bounded", bomb, packed, len(packed) * 2)
    )
    for count in (200, 20000):
        raw = b"".join(
            _json(
                {
                    "s": i,
                    "w": float(i),
                    "m": float(i),
                    "k": "tick",
                    "p": {"dt": 1.0, "energy": i % 100 / 100, "text": "café 🎹"},
                    "h": i,
                }
            )
            + b"\n"
            for i in range(count)
        )
        for members in (1, 2):
            packed = _journal_bytes(gzip, raw, 100, members)
            cases.append(
                Case(
                    f"journal-replay-{count}-{members}-members",
                    "journal-read",
                    raw,
                    packed,
                    members=members,
                )
            )
        cases.append(
            Case(f"journal-write-flush-{count}", "journal-write", raw, b"", members=1)
        )
    return cases


def _save_fixtures(root: Path, output: Path) -> None:
    cases = []
    for case in _cases():
        entry = asdict(case)
        entry.update(
            raw=base64.b64encode(case.raw).decode(),
            packed=base64.b64encode(case.packed).decode(),
            raw_sha256=_sha(case.raw),
            packed_sha256=_sha(case.packed),
        )
        cases.append(entry)
    document = {
        "schema": 1,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "source_contracts": _source_contracts(root),
        "method": {
            "query": "ijson.parse(use_float=True), items(item), validate dict rows in batches of100, exhaust EOF",
            "upload": "tell; seek0; digest.Stream+count in256KiB reads; seek0; count/discard256KiB reads; restore tell",
            "http": "one GzipFile.read(max_bytes+1), including bounded expansion refusal",
            "journal": "one write per pre-encoded entry; flush per100-row batch; reopen appends a member; replay strips and parses each line",
            "excluded": "RPC/async dispatch, disk latency, dataset selection policy, journal state reconstruction; gzip timestamp frozen at0",
            "request_compression_bypass": "gzip_encode and _GzipPrefix call stdlib zlib directly; these sources are recorded but are outside gzipx acceleration",
        },
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(document, separators=(",", ":")) + "\n").encode()
    output.write_bytes(
        gzip.compress(payload, mtime=0) if output.suffix == ".gz" else payload
    )


def _load_fixtures(path: Path) -> tuple[list[Case], dict[str, Any]]:
    payload = path.read_bytes()
    document = json.loads(gzip.decompress(payload) if path.suffix == ".gz" else payload)
    cases = []
    for item in document["cases"]:
        raw = base64.b64decode(item["raw"], validate=True)
        packed = base64.b64decode(item["packed"], validate=True)
        if _sha(raw) != item["raw_sha256"] or _sha(packed) != item["packed_sha256"]:
            message = f"fixture hash mismatch: {item['name']}"
            raise ValueError(message)
        if item["operation"] != "journal-write" and gzip.decompress(packed) != raw:
            message = f"fixture gzip bytes do not decode to source: {item['name']}"
            raise ValueError(message)
        cases.append(
            Case(
                item["name"],
                item["operation"],
                raw,
                packed,
                item["budget"],
                item["batch_rows"],
                item["members"],
            )
        )
    return cases, document


def _operation(codec: Any, case: Case) -> Callable[[], object]:
    if case.operation == "journal-write":
        return lambda: _journal_bytes(codec, case.raw, case.batch_rows, case.members)
    if case.operation == "bounded":

        def bounded() -> bytes:
            with codec.GzipFile(fileobj=io.BytesIO(case.packed)) as source:
                result: bytes = source.read(case.budget + 1)
                return result

        return bounded
    if case.operation == "query":
        ijson = cast("_Ijson", importlib.import_module("ijson"))

        def query() -> tuple[int, int]:
            count = 0
            batches = 0
            with codec.GzipFile(fileobj=io.BytesIO(case.packed)) as source:
                events = ijson.parse(source, use_float=True)
                if next(events, None) != ("", "start_array", None):
                    message = "source dataset must be a row array"
                    raise ValueError(message)
                for batch in batched(ijson.items(events, "item"), case.batch_rows):
                    if not all(isinstance(row, dict) for row in batch):
                        message = "source dataset contains an invalid row"
                        raise ValueError(message)
                    count += len(batch)
                    batches += 1
            return count, batches

        return query
    if case.operation == "upload":
        from unstd.crypto import digest

        def upload() -> tuple[bytes, int, int, int]:
            with codec.GzipFile(fileobj=io.BytesIO(case.packed)) as source:
                position = source.tell()
                try:
                    source.seek(0)
                    hashing, size = digest.Stream(), 0
                    while chunk := source.read(256 * 1024):
                        size += len(chunk)
                        hashing.update(chunk)
                    source.seek(0)
                    sent = 0
                    while chunk := source.read(256 * 1024):
                        sent += len(chunk)
                    result = hashing.raw(), size, sent
                finally:
                    source.seek(position)
                return *result, source.tell()

        return upload
    if case.operation == "journal-read":
        from unstd.serde import jsonx

        def journal() -> list[object]:
            entries = []
            with codec.GzipFile(fileobj=io.BytesIO(case.packed)) as source:
                for line in source:
                    line = line.strip()
                    if line:
                        entries.append(jsonx.loads(line))
            return entries

        return journal
    message = f"unknown Billy gzip workload: {case.operation}"
    raise ValueError(message)


def _validate(codec: Any, case: Case, actual: object) -> None:
    if case.operation == "journal-write":
        valid = type(actual) is bytes and gzip.decompress(actual) == case.raw
    elif case.operation == "bounded":
        valid = actual == case.raw[: case.budget + 1]
    elif case.operation == "query":
        rows = json.loads(case.raw)
        valid = actual == (
            len(rows),
            (len(rows) + case.batch_rows - 1) // case.batch_rows,
        )
        with codec.GzipFile(fileobj=io.BytesIO(case.packed)) as source:
            position = 0
            while chunk := source.read(64 * 1024):
                if chunk != case.raw[position : position + len(chunk)]:
                    valid = False
                position += len(chunk)
            valid = valid and position == len(case.raw)
    elif case.operation == "upload":
        from blake3 import blake3

        from bench.profiles import gzip_reads

        valid = actual == (blake3(case.raw).digest(), len(case.raw), len(case.raw), 0)
        gzip_reads._validate_upload_bytes(
            codec, gzip_reads._Case(case.name, case.raw, case.packed, "upload")
        )
    else:
        valid = actual == [
            json.loads(line) for line in case.raw.splitlines() if line.strip()
        ]
    if not valid:
        message = f"{case.name}: decoded output changed"
        raise AssertionError(message)


def _worker(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    source = source / "src" if (source / "src/unstd").exists() else source
    sys.path.insert(0, str(source))
    codec = gzip if args.stdlib else importlib.import_module("unstd.compress.gzipx")
    assert codec.__file__ is not None
    if (
        not args.stdlib
        and Path(codec.__file__).resolve() != source / "unstd/compress/gzipx.py"
    ):
        message = "requested gzip source was not imported"
        raise RuntimeError(message)
    from bench.core.timing import measure
    from bench.profiles import gzip_reads

    cases, fixtures = _load_fixtures(args.fixtures)
    if args.case:
        cases = [
            case
            for case in cases
            if any(fnmatchcase(case.name, pattern) for pattern in args.case)
        ]
    if not cases:
        message = "no Billy gzip cases selected"
        raise ValueError(message)
    records = {}
    for case in cases:
        function = _operation(codec, case)
        _validate(codec, case, function())
        calls = gzip_reads._native_calls(function)
        if not args.stdlib and not any(
            name.startswith(("zlib_ng", "unstd.compress._gzip_native"))
            for name in calls
        ):
            message = f"{case.name}: gzip native kernel was not exercised"
            raise RuntimeError(message)
        result = asdict(measure(function, rounds=args.rounds))
        _validate(codec, case, function())
        records[case.name] = {
            **result,
            "native_calls": calls,
            "raw_sha256": _sha(case.raw),
            "packed_sha256": _sha(case.packed),
        }
    return {
        "cases": records,
        "metadata": gzip_reads._metadata(
            codec, [gzip_reads._Case(cases[0].name, cases[0].raw, cases[0].packed)]
        ),
        "ijson": version("ijson")
        if any(case.operation == "query" for case in cases)
        else None,
        "source_contracts": fixtures["source_contracts"],
    }


def _run(args: argparse.Namespace) -> None:
    sources = {"candidate": args.source}
    if args.baseline_source is not None:
        sources["baseline"] = args.baseline_source
    if args.stdlib:
        sources["stdlib"] = args.source
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in sources}
    for repetition in range(args.repetitions):
        names = list(sources)
        for name in names if repetition % 2 == 0 else names[::-1]:
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "worker",
                "--source",
                str(sources[name].resolve()),
                "--fixtures",
                str(args.fixtures.resolve()),
                "--rounds",
                str(args.rounds),
            ]
            if name == "stdlib":
                command.append("--stdlib")
            for pattern in args.case:
                command.extend(("--case", pattern))
            completed = subprocess.run(  # noqa: S603 — this interpreter and explicit trusted fixture/source paths
                command, capture_output=True, text=True, check=True, timeout=180
            )
            records[name].append(json.loads(completed.stdout))
    comparisons: dict[str, dict[str, Any]] = {}
    if "baseline" in records:
        for case in records["candidate"][0]["cases"]:
            pairs = [
                {
                    "repetition": repetition,
                    "candidate_ns": candidate["cases"][case]["per_op_ns"],
                    "baseline_ns": baseline["cases"][case]["per_op_ns"],
                    "candidate_over_baseline": (
                        candidate["cases"][case]["per_op_ns"]
                        / baseline["cases"][case]["per_op_ns"]
                    ),
                }
                for repetition, (candidate, baseline) in enumerate(
                    zip(records["candidate"], records["baseline"], strict=True)
                )
            ]
            comparisons[case] = {
                "paired_candidate_over_baseline": statistics.median(
                    pair["candidate_over_baseline"] for pair in pairs
                ),
                "pairs": pairs,
            }
    report: dict[str, Any] = {
        "schema": 1,
        "estimator": "median of matched repetition candidate/baseline wall-clock ratios; independent latency medians retained",
        "recorded_utc": datetime.now(UTC).isoformat(),
        "harness_sha256": _sha(Path(__file__).read_bytes()),
        "fixture_manifest_sha256": _sha(args.fixtures.read_bytes()),
        "records": records,
        "comparisons": comparisons,
        "summary_cpu_ns": {
            name: {
                case: statistics.median(
                    sample
                    for record in runs
                    for sample in record["cases"][case]["cpu_samples_ns"]
                    if sample is not None
                )
                for case in runs[0]["cases"]
            }
            for name, runs in records.items()
        },
        "summary_wall_ns": {
            name: {
                case: statistics.median(
                    record["cases"][case]["per_op_ns"] for record in runs
                )
                for case in runs[0]["cases"]
            }
            for name, runs in records.items()
        },
    }
    maximum = args.max_slowdown
    report["max_slowdown"] = maximum
    report["regressions"] = (
        [
            name
            for name, comparison in comparisons.items()
            if comparison["paired_candidate_over_baseline"] > maximum
        ]
        if maximum is not None
        else []
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if report["regressions"]:
        message = f"Billy gzip latency regressions: {report['regressions']}"
        raise RuntimeError(message)


def main() -> None:
    """Generate frozen fixtures or run isolated paired measurements."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fixtures = commands.add_parser("fixtures")
    fixtures.add_argument("--billy-source", type=Path, required=True)
    fixtures.add_argument("--output", type=Path, required=True)
    for name in ("run", "worker"):
        command = commands.add_parser(name)
        command.add_argument("--source", type=Path, required=True)
        command.add_argument("--fixtures", type=Path, required=True)
        command.add_argument("--stdlib", action="store_true")
        command.add_argument("--rounds", type=int, default=7)
        command.add_argument(
            "--case",
            action="append",
            default=[],
            help="repeatable case-name glob selection",
        )
        if name == "run":
            command.add_argument("--baseline-source", type=Path)
            command.add_argument("--repetitions", type=int, default=3)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument(
                "--max-slowdown",
                type=float,
                help="fail after retaining evidence if a paired wall-time ratio exceeds this limit",
            )
    args = parser.parse_args()
    if args.command == "fixtures":
        _save_fixtures(args.billy_source, args.output)
    elif args.rounds < 2 or (args.command == "run" and args.repetitions < 1):
        parser.error("rounds must be at least2 and repetitions positive")
    elif (
        args.command == "run"
        and args.max_slowdown is not None
        and (
            args.baseline_source is None
            or not math.isfinite(args.max_slowdown)
            or args.max_slowdown < 1
        )
    ):
        parser.error("max-slowdown requires a baseline and a finite ratio of at least1")
    elif args.command == "worker":
        print(json.dumps(_worker(args)))
    else:
        _run(args)


if __name__ == "__main__":
    main()
