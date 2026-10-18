"""NDJSON v1: selector modulo three; remainder is raw bytes/text or records.

Modes 0/1 read physical LF-delimited bytes/UTF-8-surrogate text. Mode 2 derives
at most 16 finite rows, each with at most 64 input bytes encoded as hex, and
checks all writers and readers. Raw input is limited to 512 physical lines and
the JSON target's lexical depth/digit bounds. Every parse uses an independent
stdlib per-line model, including exotic surrounding whitespace retry, blank
lines, exact rejected physical line numbers, causal errors, callback order,
strict stopping, skip continuation and callback exception identity. No input
selects executable hooks. Output is bounded by 4096 bytes for generated rows.
"""

# Fuzz entry points run without pytest; inspect exact rejection objects directly.
# ruff: noqa: PT017

from __future__ import annotations

import io
import json as stdjson
from typing import Any

from unstd.serde import ndjson

from .json import bounded, same, text


class _CallbackError(RuntimeError):
    pass


def _model(
    lines: list[str] | list[bytes], skip: bool
) -> tuple[list[Any], list[tuple[int, ValueError]]]:
    values, failures = [], []
    for lineno, line in enumerate(lines, 1):
        if not line or line.isspace():
            continue
        try:
            parsed = stdjson.loads(line)
        except ValueError as original:
            cause = original
            stripped = line.strip()
            if stripped != line:
                try:
                    parsed = stdjson.loads(stripped)
                except ValueError as stripped_error:
                    cause = stripped_error
                else:
                    values.append(parsed)
                    continue
            failures.append((lineno, cause))
            if not skip:
                break
        else:
            values.append(parsed)
    return values, failures


def _failure(
    actual: ndjson.NDJSONDecodeError, expected: tuple[int, ValueError]
) -> None:
    lineno, cause = expected
    assert actual.lineno == lineno
    assert type(actual.err) is type(cause)
    assert actual.err.args == cause.args
    assert str(actual) == f"line {lineno}: {cause}"


def _read(source: str | bytes) -> None:
    lines = source.split("\n") if isinstance(source, str) else source.split(b"\n")
    for skip in (False, True):
        expected, failures = _model(lines, skip)
        calls = []
        yielded = []
        try:
            for row in ndjson.iter_loads(
                lines, skip_errors=skip, on_error=calls.append
            ):
                yielded.append(row)  # noqa: PERF402 — retain the prefix if iteration raises
        except ndjson.NDJSONDecodeError as error:
            assert not skip
            assert failures
            _failure(error, failures[0])
            assert error.__cause__ is error.err
            assert calls[-1] is error
        else:
            assert skip or not failures
        same(yielded, expected)
        assert len(calls) == len(failures)
        for call, expected_failure in zip(calls, failures, strict=True):
            _failure(call, expected_failure)

        observed = []
        try:
            rows = ndjson.loads(source, skip_errors=skip, on_error=observed.append)
        except ndjson.NDJSONDecodeError as error:
            assert not skip
            assert failures
            _failure(error, failures[0])
        else:
            assert skip or not failures
            same(rows, expected)
        assert len(observed) == len(failures)
        for call, expected_failure in zip(observed, failures, strict=True):
            _failure(call, expected_failure)

        # The no-hook strict fast path must reject the same first physical row.
        if not skip:
            try:
                rows = ndjson.loads(source)
            except ndjson.NDJSONDecodeError as error:
                assert failures
                _failure(error, failures[0])
            else:
                assert not failures
                same(rows, expected)

        stream = io.StringIO(source) if isinstance(source, str) else io.BytesIO(source)
        stream_lines = list(stream)
        stream.seek(0)
        stream_values, stream_failures = _model(stream_lines, skip)
        try:
            rows = ndjson.load(stream, skip_errors=skip)
        except ndjson.NDJSONDecodeError as error:
            assert not skip
            assert stream_failures
            _failure(error, stream_failures[0])
        else:
            assert skip or not stream_failures
            same(rows, stream_values)

    _, failures = _model(lines, False)
    if failures:
        sentinel = _CallbackError("fuzz callback sentinel")
        seen = []

        def stop(error: ndjson.NDJSONDecodeError) -> None:
            seen.append(error)
            raise sentinel

        try:
            ndjson.loads(source, skip_errors=True, on_error=stop)
        except _CallbackError as error:
            assert error is sentinel
        else:
            msg = "NDJSON swallowed caller callback failure"
            raise AssertionError(msg)
        assert len(seen) == 1
        _failure(seen[0], failures[0])


def fuzz(data: bytes) -> None:
    """Compare independent line/error models and real streaming/writing APIs."""
    selector, raw = (data[0], data[1:]) if data else (0, b"")
    mode = selector % 3
    if mode == 2:
        rows = [
            {"index": index, "value": raw[offset : offset + 64].hex()}
            for index, offset in enumerate(range(0, min(len(raw), 1024), 64))
        ]
        expected = b"".join(
            stdjson.dumps(row, ensure_ascii=False, separators=(",", ":")).encode()
            + b"\n"
            for row in rows
        )
        assert len(expected) <= 4096
        assert ndjson.dumpb(iter(rows)) == expected
        assert ndjson.dumps(iter(rows)) == expected.decode()
        for stream in (io.BytesIO(), io.StringIO()):
            assert ndjson.dump(iter(rows), stream) == len(rows)
            assert stream.getvalue() == (
                expected if isinstance(stream, io.BytesIO) else expected.decode()
            )
        _read(expected)
        _read(expected.decode())
        return
    if raw.count(b"\n") >= 512:
        return
    source = text(raw) if mode == 1 else raw
    lines = source.split("\n") if isinstance(source, str) else source.split(b"\n")
    if any(not bounded(line) for line in lines):
        return
    _read(source)
