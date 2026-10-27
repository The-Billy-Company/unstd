"""Source-backed persistence proof for Consumer's actual tool-corpus loader.

Run with ``--consumer-source /path/to/consumer --output /tmp/consumer-cache.json``.
Synthetic JSONL inputs exercise the unchanged ``load_tool_use_corpus`` function
from that checkout. Four fresh processes prove cold production, warm reuse,
changed-content invalidation, and explicit validator-version invalidation.
Input and loader hashing are included in each timed operation.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import file_digest, sha256
import importlib.util
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from typing import TYPE_CHECKING, Any, cast

import unstd.cache as cache_module
from unstd.cache import Cache


if TYPE_CHECKING:
    from collections.abc import Callable

    from unstd.serde.jsonx import Json


_LOADER = Path("benchmarks/harness/loaders/fixtures.py")
_PARAMETERS = {
    "type": "object",
    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["query"],
}


def _record(index: int, tools: int, revision: int) -> dict[str, Any]:
    """A deterministic valid tool-corpus row; no application data is read."""
    query = f"Find item {index}"
    if revision and index == 0:
        query = "Find a changed first item"
    return {
        "id": str(index),
        "query": query,
        "tools": [
            {
                "name": f"lookup{number}",
                "description": "Search synthetic inventory metadata",
                "parameters": _PARAMETERS,
            }
            for number in range(tools)
        ],
        "expected_calls": [
            {"name": "lookup0", "arguments": {"query": f"item {index}"}}
        ],
    }


def _prepare(directory: Path, rows: int, tools: int) -> dict[str, int]:
    """Base and changed-content corpora, prepared outside measurement."""
    sizes = {}
    for name, revision in (("base.jsonl", 0), ("changed.jsonl", 1)):
        path = directory / name
        with path.open("w", encoding="utf-8") as output:
            output.write("# Synthetic source-backed cache proof\n\n")
            for index in range(rows):
                output.write(json.dumps(_record(index, tools, revision)) + "\n")
        sizes[name] = path.stat().st_size
    return sizes


def _file_hash(path: Path) -> str:
    """Stream all source input bytes before deciding whether a result is reusable."""
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def _result_hash(value: object) -> str:
    """An independent stdlib output receipt, outside the timed region."""
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _cache_hashes() -> dict[str, str]:
    """The package source loaded by this interpreter, including lock helpers."""
    directory = Path(cache_module.__file__).parent
    return {
        path.relative_to(directory).as_posix(): _file_hash(path)
        for path in sorted(directory.rglob("*.py"))
    }


def _key(content_hash: str, loader_hash: str, validator_version: str) -> str:
    """Every source-content and validation-version input in one explicit key."""
    identity = {
        "corpus_sha256": content_hash,
        "loader_sha256": loader_hash,
        "validator_version": validator_version,
    }
    return "tool-corpus:" + _result_hash(identity)


def _load_loader(path: Path) -> Callable[[Path], list[dict[str, Any]]]:
    """The actual source module and function, executed without source extraction."""
    spec = importlib.util.spec_from_file_location("_consumer_cache_proof_loader", path)
    if spec is None or spec.loader is None:
        message = f"cannot load Consumer's fixture loader: {path}"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast("Callable[[Path], list[dict[str, Any]]]", module.load_tool_use_corpus)


def _worker(
    directory: Path,
    consumer_source: Path,
    rows: int,
    tools: int,
    corpus_name: str,
    revision: int,
    validator_version: str,
) -> dict[str, Any]:
    """One fresh application process, with a complete independent value oracle."""
    expected = [_record(index, tools, revision) for index in range(rows)]
    loader_path = consumer_source / _LOADER
    admitted_source_hash = _file_hash(loader_path)
    admitted_cache_hashes = _cache_hashes()
    load = _load_loader(loader_path)
    corpus_path = directory / corpus_name
    producer_calls = 0

    def produce() -> Json:
        nonlocal producer_calls
        producer_calls += 1
        return cast("Json", load(corpus_path))

    started = time.perf_counter()
    loader_hash = _file_hash(loader_path)
    content_hash = _file_hash(corpus_path)
    key = _key(content_hash, loader_hash, validator_version)
    cache = Cache(directory / "results.sqlite3", namespace="consumer-fixtures-v1")
    value = cache.get_or_compute(key, produce)
    elapsed = time.perf_counter() - started
    if value != expected:
        message = (
            "Consumer loader or cached result differs from generated expected rows"
        )
        raise AssertionError(message)
    if loader_hash != admitted_source_hash or _file_hash(loader_path) != loader_hash:
        message = "Consumer loader source changed during measurement"
        raise AssertionError(message)
    if _file_hash(corpus_path) != content_hash:
        message = "synthetic corpus changed during measurement"
        raise AssertionError(message)
    if _cache_hashes() != admitted_cache_hashes:
        message = "cache package source changed during measurement"
        raise AssertionError(message)
    return {
        "pid": os.getpid(),
        "producer_calls": producer_calls,
        "wall_seconds": elapsed,
        "key": key,
        "validator_version": validator_version,
        "loader_sha256": loader_hash,
        "corpus_sha256": content_hash,
        "result_sha256": _result_hash(value),
        "expected_sha256": _result_hash(expected),
        "cache_source_sha256": admitted_cache_hashes,
        "exact_expected_rows_match": True,
    }


def _run_worker(
    directory: Path,
    consumer_source: Path,
    rows: int,
    tools: int,
    *,
    corpus_name: str = "base.jsonl",
    revision: int = 0,
    validator_version: str = "schema-v1",
) -> dict[str, Any]:
    """A bounded process using the selected interpreter and its unstd install."""
    result = subprocess.run(  # noqa: S603 — owned interpreter and literal module
        [
            sys.executable,
            "-m",
            "bench.profiles.consumer_cache",
            "--consumer-source",
            str(consumer_source),
            "--worker-directory",
            str(directory),
            "--rows",
            str(rows),
            "--tools",
            str(tools),
            "--corpus-name",
            corpus_name,
            "--revision",
            str(revision),
            "--validator-version",
            validator_version,
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return cast("dict[str, Any]", json.loads(result.stdout))


def main() -> None:
    """Record source-backed reuse, invalidation, and host-specific elapsed time."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer-source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rows", type=int, default=16_000)
    parser.add_argument("--tools", type=int, default=8)
    parser.add_argument("--worker-directory", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--corpus-name", default="base.jsonl", help=argparse.SUPPRESS)
    parser.add_argument("--revision", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument(
        "--validator-version", default="schema-v1", help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    consumer_source = args.consumer_source.resolve()
    if not (consumer_source / _LOADER).is_file():
        parser.error(f"Consumer source lacks {_LOADER}")
    if args.rows <= 0 or args.tools <= 0:
        parser.error("rows and tools must be positive")
    if args.worker_directory is not None:
        print(
            json.dumps(
                _worker(
                    args.worker_directory,
                    consumer_source,
                    args.rows,
                    args.tools,
                    args.corpus_name,
                    args.revision,
                    args.validator_version,
                )
            )
        )
        return
    with TemporaryDirectory(prefix="unstd-consumer-cache-") as temporary:
        directory = Path(temporary)
        sizes = _prepare(directory, args.rows, args.tools)
        cold = _run_worker(directory, consumer_source, args.rows, args.tools)
        warm = _run_worker(directory, consumer_source, args.rows, args.tools)
        changed_input = _run_worker(
            directory,
            consumer_source,
            args.rows,
            args.tools,
            corpus_name="changed.jsonl",
            revision=1,
        )
        changed_version = _run_worker(
            directory,
            consumer_source,
            args.rows,
            args.tools,
            validator_version="schema-v2",
        )
        assert (
            len({run["pid"] for run in (cold, warm, changed_input, changed_version)})
            == 4
        ), "proof requires four distinct processes"
        assert cold["producer_calls"] == 1
        assert warm["producer_calls"] == 0
        assert changed_input["producer_calls"] == 1
        assert changed_version["producer_calls"] == 1
        assert cold["key"] == warm["key"]
        assert len({cold["key"], changed_input["key"], changed_version["key"]}) == 3
        assert cold["result_sha256"] == warm["result_sha256"]
        assert cold["result_sha256"] == changed_version["result_sha256"]
        assert cold["result_sha256"] != changed_input["result_sha256"]
        assert all(
            run["cache_source_sha256"] == cold["cache_source_sha256"]
            for run in (warm, changed_input, changed_version)
        ), "proof phases used different cache source"
        assert _cache_hashes() == cold["cache_source_sha256"]
        report = {
            "schema": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "workload": "actual Consumer load_tool_use_corpus on synthetic JSONL",
            "command": (
                "python -m bench.profiles.consumer_cache --consumer-source PATH "
                f"--rows {args.rows} --tools {args.tools} --output PATH"
            ),
            "source": {
                "path": _LOADER.as_posix(),
                "function": "load_tool_use_corpus",
                "file_sha256": cold["loader_sha256"],
                "module_executed_unchanged": True,
            },
            "environment": {
                "python": sys.version,
                "platform": platform.platform(),
                "machine": platform.machine(),
                "sqlite": sqlite3.sqlite_version,
            },
            "proof_sha256": _file_hash(Path(__file__)),
            "cache_source_sha256": cold["cache_source_sha256"],
            "fixture": {
                "rows": args.rows,
                "tools_per_row": args.tools,
                "tools_validated_per_cold_call": args.rows * args.tools,
                "corpus_bytes": sizes,
            },
            "cold": cold,
            "warm": warm,
            "changed_input": changed_input,
            "changed_validator_version": changed_version,
            "warm_speedup": cold["wall_seconds"] / warm["wall_seconds"],
            "database_bytes": (directory / "results.sqlite3").stat().st_size,
            "verified": {
                "four_distinct_processes": True,
                "all_rows_equal_independent_expectations": True,
                "warm_producer_calls_zero": True,
                "same_warm_output_digest": True,
                "changed_content_misses": True,
                "changed_validator_version_misses": True,
                "input_and_source_hashing_timed": True,
            },
            "limits": (
                "Synthetic inputs and one host. Actual Consumer loader code runs "
                "unchanged; no application migration or live service speedup is "
                "claimed. Fixture generation, expected-result construction, module "
                "import, process startup and output validation are outside timing. "
                "Source/input hashing, cache initialization, lookup, decoding, "
                "cold parsing/validation/encoding and committed writes are timed. "
                "No sleeps simulate producer work. Timing is diagnostic."
            ),
        }
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
