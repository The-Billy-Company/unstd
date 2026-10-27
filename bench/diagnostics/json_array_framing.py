"""Independent JSON-array framing checks with reproducible bounded workloads.

Run ``python bench/diagnostics/json_array_framing.py --output results.json``.
The default corpus takes roughly ten seconds on the development host. Counts,
seeds, corpus/source hashes, raw scaling samples and memory peaks are retained;
timings describe this host rather than a machine-independent performance gate.
"""

from __future__ import annotations

import argparse
import gc
from hashlib import sha256
import io
import itertools
import json
from pathlib import Path
import platform
import random
from statistics import median
import sys
import time
import tracemalloc
from typing import Any

from unstd.serde import _stream, jsonx


_ESCAPE_ALPHABET = '"\\n0u[],{}a \n\r\t'
_MALFORMED_ALPHABET = '[]{}",\\nt01af: \n\t\r'
_ENCODINGS = (
    "utf-8",
    "utf-8-sig",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "utf-32",
    "utf-32-le",
    "utf-32-be",
)


def _same(actual: Any, expected: Any) -> None:
    """Compare exact JSON types, order and float spellings recursively."""
    assert type(actual) is type(expected)
    if isinstance(expected, float):
        assert actual.hex() == expected.hex()
    elif isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            _same(left, right)
    elif isinstance(expected, dict):
        assert list(actual) == list(expected)
        for key in expected:
            _same(actual[key], expected[key])
    else:
        assert actual == expected


def _escaped_strings() -> dict[str, Any]:
    """Exhaust short source strings whose encoded forms stress escape framing."""
    chunks = (1, 2, 3, 4, 7)
    corpus = sha256()
    values = 0
    for length in range(5):
        for characters in itertools.product(_ESCAPE_ALPHABET, repeat=length):
            value = "".join(characters)
            document = json.dumps([value, {"a": value}, [value]], ensure_ascii=False)
            corpus.update(document.encode())
            corpus.update(b"\n")
            expected = json.loads(document)
            for size in chunks:
                _same(
                    list(jsonx.iterload(io.StringIO(document), chunk_size=size)),
                    expected,
                )
            values += 1
    return {
        "source_alphabet": _ESCAPE_ALPHABET,
        "source_lengths": [0, 1, 2, 3, 4],
        "chunk_sizes": chunks,
        "source_strings": values,
        "documents_checked": values * len(chunks),
        "corpus_sha256": corpus.hexdigest(),
        "mismatches": 0,
    }


def _malformed_documents(count: int, seed: int) -> dict[str, Any]:
    """Compare seeded random valid/malformed arrays with the stdlib oracle."""
    generator = random.Random(seed)
    corpus = sha256()
    valid = invalid = 0
    for _ in range(count):
        document = (
            "["
            + "".join(generator.choices(_MALFORMED_ALPHABET, k=generator.randrange(50)))
            + "]"
        )
        size = generator.randrange(1, 15)
        corpus.update(json.dumps([size, document]).encode())
        corpus.update(b"\n")
        try:
            expected = json.loads(document)
        except (json.JSONDecodeError, RecursionError):
            invalid += 1
            try:
                list(jsonx.iterload(io.StringIO(document), chunk_size=size))
            except (json.JSONDecodeError, RecursionError):
                pass
            else:
                message = f"streaming accepted malformed document: {document!r}"
                raise AssertionError(message)
        else:
            valid += 1
            _same(
                list(jsonx.iterload(io.StringIO(document), chunk_size=size)), expected
            )
    return {
        "seed": seed,
        "alphabet": _MALFORMED_ALPHABET,
        "random_payload_length_range": [0, 49],
        "chunk_size_range": [1, 14],
        "documents_checked": count,
        "valid_documents": valid,
        "invalid_documents": invalid,
        "corpus_sha256": corpus.hexdigest(),
        "mismatches": 0,
    }


def _binary_encodings() -> dict[str, Any]:
    """Check binary surrogate handling across every small code-unit boundary."""
    values = [
        "before\ud800after",
        "\udc00",
        "literal\ud800\udc00",
        "é😀界",
        'x\\n" []{}',
    ]
    document = json.dumps(values, ensure_ascii=False)
    corpus = sha256()
    for encoding in _ENCODINGS:
        raw = document.encode(encoding, errors="surrogatepass")
        corpus.update(encoding.encode())
        corpus.update(b"\0")
        corpus.update(raw)
        expected = json.loads(raw)
        for size in range(1, 21):
            _same(list(jsonx.iterload(io.BytesIO(raw), chunk_size=size)), expected)
    return {
        "encodings": _ENCODINGS,
        "chunk_size_range": [1, 20],
        "documents_checked": len(_ENCODINGS) * 20,
        "corpus_sha256": corpus.hexdigest(),
        "mismatches": 0,
    }


class _WhitespaceReader:
    """Generate a large inter-item whitespace run without retaining that run."""

    def __init__(self, count: int, *, malformed: bool = False) -> None:
        self.remaining = count
        self.stage = 0
        self.malformed = malformed

    def read(self, size: int, /) -> str:
        """Emit a short prefix, whitespace chunks and a valid/invalid suffix."""
        assert 0 < size <= 127
        if self.stage == 0:
            self.stage = 1
            return "[1"
        if self.remaining:
            count = min(size, self.remaining)
            self.remaining -= count
            return " " * count
        if self.stage == 1:
            self.stage = 2
            return "2]" if self.malformed else ",2]"
        return ""


def _whitespace_memory() -> dict[str, Any]:
    """Retain raw traced peaks as whitespace grows by three orders of magnitude."""
    samples = []
    for count in (10_000, 1_000_000, 10_000_000):
        reader = _WhitespaceReader(count)
        tracemalloc.start()
        try:
            actual = list(jsonx.iterload(reader, chunk_size=127))
            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert actual == [1, 2]
        samples.append(
            {"whitespace_bytes": count, "current_bytes": current, "peak_bytes": peak}
        )
    try:
        list(
            jsonx.iterload(_WhitespaceReader(1_000_000, malformed=True), chunk_size=127)
        )
    except json.JSONDecodeError:
        pass
    else:
        message = "discarded whitespace incorrectly joined adjacent number tokens"
        raise AssertionError(message)
    return {
        "chunk_size": 127,
        "source_generated_incrementally": True,
        "tracemalloc_started_after_source_construction": True,
        "samples": samples,
        "adjacent_number_tokens_rejected": True,
    }


def _large_string_scaling() -> dict[str, Any]:
    """Measure growing single items without reparsing their accumulated prefix."""
    cases = []
    for size in (100_000, 400_000, 1_600_000, 6_400_000):
        document = '["' + "a" * size + '"]'
        samples = []
        for _ in range(5):
            reader = io.StringIO(document)
            cpu_start = time.thread_time_ns()
            wall_start = time.perf_counter_ns()
            result = list(jsonx.iterload(reader, chunk_size=1024))
            wall_ns = time.perf_counter_ns() - wall_start
            cpu_ns = time.thread_time_ns() - cpu_start
            assert len(result) == 1
            assert result[0] == "a" * size
            samples.append({"wall_ns": wall_ns, "thread_cpu_ns": cpu_ns})
        cases.append(
            {
                "string_bytes": size,
                "samples": samples,
                "median_wall_ns": median(row["wall_ns"] for row in samples),
                "median_thread_cpu_ns": median(row["thread_cpu_ns"] for row in samples),
            }
        )
    return {
        "chunk_size": 1024,
        "fixtures_and_reader_construction_outside_timing": True,
        "result_validation_outside_timing": True,
        "gc_enabled": gc.isenabled(),
        "cases": cases,
    }


def main() -> None:
    """Run independent framing probes and save their reproducible evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--random-documents", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=91)
    args = parser.parse_args()
    if args.random_documents < 1:
        parser.error("--random-documents must be positive")
    paths = [Path(__file__).resolve(), Path(jsonx.__file__), Path(_stream.__file__)]
    hashes_before = {str(path): sha256(path.read_bytes()).hexdigest() for path in paths}
    started = time.perf_counter_ns()
    report = {
        "schema_version": 1,
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "msgspec_available": jsonx._HAVE_MSGSPEC,
        "source_sha256": hashes_before,
        "escaped_strings": _escaped_strings(),
        "seeded_differential": _malformed_documents(args.random_documents, args.seed),
        "binary_encodings": _binary_encodings(),
        "whitespace_memory": _whitespace_memory(),
        "large_string_scaling": _large_string_scaling(),
        "elapsed_wall_ns": time.perf_counter_ns() - started,
    }
    assert hashes_before == {
        str(path): sha256(path.read_bytes()).hexdigest() for path in paths
    }, "source changed during diagnostics"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Framing checks passed; evidence saved to {args.output}")


if __name__ == "__main__":
    main()
