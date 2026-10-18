"""Scripted stream interruptions with actual bytes, progress, and recovery.

Every failpoint must fire; exceptions crossing the facade retain their identity.
Buffered EINTR probes distinguish an actual retry from propagation. Serialized
prefixes are checked with stdlib JSON; gzip payloads are checked with an
independent stdlib decompressor. A failed compressed write can advance the codec
before its destination raises. Discard that member and start a fresh one:
retrying the uncompressed block inside it can poison its CRC, as the dedicated
stdlib experiment records on affected interpreters.
"""

from __future__ import annotations

import errno
import gzip
import hashlib
import io
import json
from typing import TYPE_CHECKING
import zlib

import pytest

from tests.support.chaos import ordered
from tests.support.chaos_streams import BytesStream, TextStream
from unstd.compress import gzipx
from unstd.serde import jsonx, ndjson


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


ROWS = [{"id": n, "text": "payload λ", "values": [n, None]} for n in range(5)]
# Deterministic incompressible input makes mid-stream transport failures
# reachable even when the codec buffers its first input block.
PAYLOAD = b"".join(hashlib.sha256(n.to_bytes(4)).digest() for n in range(2048))
MEMBER = gzip.compress(PAYLOAD, mtime=0)
_ERRORS = [
    pytest.param(lambda: OSError(errno.ENOSPC, "scripted stream full"), id="enospc"),
    pytest.param(
        lambda: InterruptedError(errno.EINTR, "scripted interruption"), id="eintr"
    ),
    pytest.param(lambda: KeyboardInterrupt("scripted cancellation"), id="cancel"),
]
_STREAMS = [pytest.param(BytesStream, id="binary"), pytest.param(TextStream, id="text")]


@pytest.fixture
def record_stream(chaos_journal, record_property, request):
    chaos_journal.record("stream-plan", scenario=request.node.name)

    def record(name, value):
        evidence = json.loads(value)
        record_property(name, evidence)
        chaos_journal.record("stream-observation", observation=name, evidence=evidence)

    return record


def _report(record_stream: Callable, stream: BytesStream | TextStream) -> None:
    evidence = stream.schedule.evidence()
    assert evidence["fault"] is not None
    assert stream.schedule.fault is not None
    assert stream.schedule.fault.fired
    record_stream("fault_schedule", json.dumps(evidence, sort_keys=True))


def _lines(rows: list[object]) -> str:
    return "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )


@pytest.mark.parametrize("stream_type", _STREAMS)
def test_seeded_ndjson_restart_schedule_preserves_every_record(
    stream_type, chaos_seed, chaos_journal, record_stream
) -> None:
    schedule = ordered(chaos_seed, "streams.ndjson.restart.v1", [1, 2, 3, 4, 5])
    rows = [{"id": n, "seed": chaos_seed} for n in range(25)]
    sink = stream_type()
    committed = 0
    chaos_journal.record("restart-plan", operation="write", invocations=list(schedule))
    for phase, at in enumerate(schedule):
        fault = OSError(errno.ENOSPC, f"scripted storage interruption {phase}")
        sink.schedule.arm("write", fault, at=at)
        with pytest.raises(OSError, match="scripted storage interruption") as caught:
            ndjson.dump(rows[committed:], sink)
        assert caught.value is fault
        committed += at - 1
        data = sink.getvalue()
        assert [json.loads(line) for line in data.splitlines()] == rows[:committed]
        assert not sink.closed
        chaos_journal.record(
            "restart-observed",
            phase=phase,
            committed=committed,
            accepted_bytes=len(data),
            fired=sink.schedule.fault.fired,
        )
    assert ndjson.dump(rows[committed:], sink) == len(rows) - committed
    assert [json.loads(line) for line in sink.getvalue().splitlines()] == rows
    _report(record_stream, sink)


@pytest.mark.parametrize("stream_type", _STREAMS)
@pytest.mark.parametrize("at", [1, 2, 4])
@pytest.mark.parametrize("make_error", _ERRORS)
def test_ndjson_write_interruption_keeps_complete_prefix_and_resumes(
    stream_type, at, make_error, record_stream
) -> None:
    sink = stream_type()
    fault = make_error()
    sink.schedule.arm("write", fault, at=at)
    produced: list[int] = []

    def rows() -> Iterator[dict]:
        for row in ROWS:
            produced.append(row["id"])
            yield row

    with pytest.raises(type(fault)) as caught:
        ndjson.dump(rows(), sink)
    assert caught.value is fault
    assert produced == list(range(at))
    prefix = sink.getvalue()
    if isinstance(prefix, bytes):
        prefix = prefix.decode()
    assert [json.loads(line) for line in prefix.splitlines()] == ROWS[: at - 1]
    assert prefix.endswith("\n") if at > 1 else prefix == ""
    assert not sink.closed
    assert sink.schedule.counts["flush"] == sink.schedule.counts["close"] == 0
    assert ndjson.dump(ROWS[at - 1 :], sink) == len(ROWS) - at + 1
    sink.seek(0)
    assert ndjson.load(sink) == ROWS
    _report(record_stream, sink)


@pytest.mark.parametrize("stream_type", _STREAMS)
@pytest.mark.parametrize("at", [1, 3, 5])
@pytest.mark.parametrize("skip_errors", [False, True], ids=["strict", "skip-invalid"])
@pytest.mark.parametrize("make_error", _ERRORS)
def test_ndjson_transport_fault_never_becomes_a_rejected_row(
    stream_type, at, skip_errors, make_error, record_stream
) -> None:
    data = _lines(ROWS)
    source = stream_type(data.encode() if stream_type is BytesStream else data)
    fault = make_error()
    source.schedule.arm("readline", fault, at=at)
    callbacks: list[ndjson.NDJSONDecodeError] = []
    iterator = ndjson.iter_loads(
        source, skip_errors=skip_errors, on_error=callbacks.append
    )
    observed = [next(iterator) for _ in range(at - 1)]
    assert observed == ROWS[: at - 1]
    with pytest.raises(type(fault)) as caught:
        next(iterator)
    assert caught.value is fault
    assert callbacks == []
    assert not source.closed
    assert ndjson.load(source) == ROWS[at - 1 :]
    assert source.schedule.counts["close"] == 0
    _report(record_stream, source)


@pytest.mark.parametrize("skip_errors", [False, True], ids=["strict", "skip-invalid"])
@pytest.mark.parametrize("bad_line", [1, 3, 5])
@pytest.mark.parametrize("make_error", _ERRORS)
def test_ndjson_callback_cancellation_observes_one_error_and_stops_consumption(
    skip_errors, bad_line, make_error, record_stream
) -> None:
    lines = _lines(ROWS).splitlines(keepends=True)
    lines[bad_line - 1] = "{malformed}\n"
    source = TextStream("".join(lines))
    fault = make_error()
    observed: list[ndjson.NDJSONDecodeError] = []

    def callback(error: ndjson.NDJSONDecodeError) -> None:
        observed.append(error)
        raise fault

    iterator = ndjson.iter_loads(source, skip_errors=skip_errors, on_error=callback)
    assert [next(iterator) for _ in range(bad_line - 1)] == ROWS[: bad_line - 1]
    with pytest.raises(type(fault)) as caught:
        next(iterator)
    assert caught.value is fault
    assert len(observed) == 1
    assert observed[0].lineno == bad_line
    assert isinstance(observed[0].err, ValueError)
    assert source.schedule.counts["readline"] == bad_line
    assert not source.closed
    assert ndjson.load(source) == ROWS[bad_line:]
    record_stream(
        "fault_schedule",
        json.dumps(
            {"version": 1, "operation": "on_error", "line": bad_line, "fired": True}
        ),
    )


@pytest.mark.parametrize("at", [1, 4, 11])
@pytest.mark.parametrize("make_error", _ERRORS)
def test_json_dump_stream_failure_matches_stdlib_prefix_and_identity(
    at, make_error, record_stream
) -> None:
    fault = make_error()
    expected = TextStream()
    expected.schedule.arm("write", fault, at=at)
    actual = TextStream()
    actual.schedule.arm("write", fault, at=at)
    for serializer, stream in [(json.dump, expected), (jsonx.dump, actual)]:
        with pytest.raises(type(fault)) as caught:
            serializer(ROWS, stream, ensure_ascii=False)
        assert caught.value is fault
        assert not stream.closed
        assert stream.schedule.counts["flush"] == stream.schedule.counts["close"] == 0
    assert actual.getvalue() == expected.getvalue()
    assert actual.schedule.evidence() == expected.schedule.evidence()
    actual.seek(0)
    actual.truncate()
    jsonx.dump(ROWS, actual, ensure_ascii=False)
    assert json.loads(actual.getvalue()) == ROWS
    _report(record_stream, actual)


@pytest.mark.parametrize("stream_type", _STREAMS)
@pytest.mark.parametrize("make_error", _ERRORS)
def test_json_load_read_failure_keeps_transport_identity_and_caller_handle(
    stream_type, make_error, record_stream
) -> None:
    data = json.dumps(ROWS)
    source = stream_type(data.encode() if stream_type is BytesStream else data)
    fault = make_error()
    source.schedule.arm("read", fault)
    with pytest.raises(type(fault)) as caught:
        jsonx.load(source)
    assert caught.value is fault
    assert source.tell() == 0
    assert not source.closed
    assert jsonx.load(source) == ROWS
    assert source.schedule.counts["read"] == 2
    assert source.schedule.counts["close"] == 0
    _report(record_stream, source)


@pytest.mark.parametrize("limit", [1, 2, 7], ids=["one-byte", "two-byte", "seven-byte"])
def test_ndjson_buffered_sink_completes_real_short_writes(limit, record_stream) -> None:
    sink = BytesStream(write_limit=limit)
    buffered = io.BufferedWriter(sink, buffer_size=16)
    try:
        assert ndjson.dump(ROWS, buffered) == len(ROWS)
        buffered.flush()
        assert [json.loads(line) for line in sink.getvalue().splitlines()] == ROWS
        assert sink.schedule.counts["write"] > len(ROWS)
        assert not sink.closed
        record_stream(
            "stream_schedule", json.dumps(sink.schedule.evidence(), sort_keys=True)
        )
    finally:
        # detach() leaves the caller-owned raw stream available to its owner.
        buffered.detach()


@pytest.mark.parametrize(
    "limit", [2, 3, 17, 257], ids=["two", "three", "seventeen", "257"]
)
def test_gzip_short_reads_preserve_member_boundaries_and_payload(
    limit, record_stream
) -> None:
    # gzip's public stream shares the stdlib two-byte magic read assumption;
    # short transfers after that must still work across concatenated members.
    data = MEMBER + gzip.compress(b"last member", mtime=0)
    source = BytesStream(data, read_limit=limit)
    with gzipx.GzipFile(fileobj=source, mode="rb") as handle:
        pieces: list[bytes] = []
        while chunk := handle.read1(997):
            pieces.append(chunk)
    assert b"".join(pieces) == PAYLOAD + b"last member"
    assert not source.closed
    assert source.schedule.counts["read"] > len(data) // limit
    record_stream(
        "stream_schedule", json.dumps(source.schedule.evidence(), sort_keys=True)
    )


@pytest.mark.parametrize(
    "read_method", ["read1", "readinto1"], ids=["bytes", "into-buffer"]
)
@pytest.mark.parametrize(
    "after", [0, 1, 3], ids=["first-read", "after-one-read", "after-three-reads"]
)
@pytest.mark.parametrize("make_error", _ERRORS)
def test_gzip_midstream_read_fault_recovers_without_duplicate_or_lost_output(
    read_method, after, make_error, record_stream
) -> None:
    source = BytesStream(MEMBER, read_limit=257)
    fault = make_error()
    with gzipx.GzipFile(fileobj=source, mode="rb") as handle:
        output = bytearray()
        buffer = bytearray(113)

        def read() -> bytes:
            if read_method == "read1":
                return handle.read1(113)
            count = handle.readinto1(buffer)
            return bytes(buffer[:count])

        for _ in range(after):
            output.extend(read())
        source.schedule.arm("read", fault)
        # Buffered decompressed bytes may be available before the next actual
        # transport read. Record every returned byte before the fault fires.
        observed_failure = None
        for _ in range(len(PAYLOAD) + 1):
            try:
                chunk = read()
            except type(fault) as caught:
                observed_failure = caught
                break
            if not chunk:
                break
            output.extend(chunk)
        else:
            pytest.fail("bounded reader did not reach its failpoint")
        if isinstance(fault, InterruptedError):
            # BufferedReader retries EINTR at its documented raw I/O boundary.
            # Pin the same behavior independently through stdlib gzip.
            assert observed_failure is None
            assert bytes(output) == PAYLOAD
            reference_source = BytesStream(MEMBER, read_limit=257)
            reference_source.schedule.arm("read", fault)
            with gzip.GzipFile(fileobj=reference_source, mode="rb") as reference:
                assert b"".join(iter(lambda: reference.read1(113), b"")) == PAYLOAD
            assert reference_source.schedule.fault.fired
        else:
            assert observed_failure is fault
        assert bytes(output) == PAYLOAD[: len(output)]
        assert not source.closed
        while chunk := read():
            output.extend(chunk)
        assert bytes(output) == PAYLOAD
    assert not source.closed
    _report(record_stream, source)


@pytest.mark.parametrize("make_error", _ERRORS)
def test_gzip_rewind_seek_fault_keeps_identity_and_allows_fresh_recovery(
    make_error, record_stream
) -> None:
    source = BytesStream(MEMBER)
    fault = make_error()
    with gzipx.GzipFile(fileobj=source, mode="rb") as handle:
        assert handle.read() == PAYLOAD
        source.schedule.arm("seek", fault)
        with pytest.raises(type(fault)) as caught:
            handle.rewind()
        assert caught.value is fault
        assert not source.closed
    source.seek(0)
    with gzipx.GzipFile(fileobj=source, mode="rb") as recovered:
        assert recovered.read() == PAYLOAD
    assert not source.closed
    _report(record_stream, source)


@pytest.mark.parametrize(
    ("operation", "make_error"),
    [
        pytest.param(operation, case.values[0], id=f"{case.id}-{operation}")
        for operation in ("write", "flush", "close")
        for case in _ERRORS
        if not (operation == "write" and case.id == "eintr")
    ],
)
def test_gzip_output_fault_propagates_and_a_fresh_member_recovers(
    operation, make_error, record_stream
) -> None:
    sink = BytesStream()
    handle = gzipx.GzipFile(fileobj=sink, mode="wb", mtime=0)
    fault = make_error()
    try:
        if operation == "write":
            sink.schedule.arm("write", fault)
            with pytest.raises(type(fault)) as caught:
                handle.write(PAYLOAD * 16)
        else:
            assert handle.write(PAYLOAD) == len(PAYLOAD)
            handle.flush()
            # Closing writes a compressor tail and RFC 1952 trailer to the
            # supplied sink; the sink itself must remain caller-owned.
            sink.schedule.arm("flush" if operation == "flush" else "write", fault)
            with pytest.raises(type(fault)) as caught:
                handle.flush() if operation == "flush" else handle.close()
        assert caught.value is fault
        assert not sink.closed
        assert sink.schedule.counts["close"] == 0
        assert sink.getvalue().startswith(b"\x1f\x8b")
    finally:
        handle.close()
    assert not sink.closed
    # A write may advance the codec before the destination throws. Restarting
    # is explicit and discards the incomplete member rather than pretending a
    # failed compressed write can be replayed safely inside the same member.
    sink.seek(0)
    sink.truncate()
    with gzipx.GzipFile(fileobj=sink, mode="wb", mtime=0) as recovered:
        assert recovered.write(PAYLOAD) == len(PAYLOAD)
    assert gzip.decompress(sink.getvalue()) == PAYLOAD
    assert not sink.closed
    _report(record_stream, sink)


def test_gzip_interrupted_raw_write_records_interpreter_retry_boundary(
    chaos_journal, record_stream
) -> None:
    # CPython's BufferedWriter can retry the complete uncompressed block after
    # a custom compressed sink raises EINTR. On affected interpreter versions
    # that acknowledges the write while producing a CRC-invalid member. The
    # native backend propagates the exception instead. Neither is a promise
    # that replaying a failed write inside the same gzip member is safe.
    observations: dict[str, dict[str, object]] = {}
    # Exceed the reviewed interpreter write-buffer sizes, including 3.14.
    probe_payload = PAYLOAD * 16
    for name, stream_type in [("stdlib", gzip.GzipFile), ("facade", gzipx.GzipFile)]:
        sink = BytesStream()
        handle = stream_type(fileobj=sink, mode="wb", mtime=0)
        fault = InterruptedError(errno.EINTR, "scripted compressed sink interruption")
        sink.schedule.arm("write", fault)
        try:
            write_result = handle.write(probe_payload)
        except InterruptedError as error:
            observed_error = error
            write_result = None
        else:
            observed_error = None
        assert observed_error is None or observed_error is fault
        assert sink.schedule.fault.fired
        assert sum("fault" in event for event in sink.schedule.events) == 1
        if observed_error is None:
            assert write_result == len(probe_payload)
        handle.close()
        assert not sink.closed
        try:
            decoded = gzip.decompress(sink.getvalue())
        except (EOFError, gzip.BadGzipFile, zlib.error) as error:
            member = type(error).__name__
        else:
            assert decoded == probe_payload
            member = "complete"
        observations[name] = {
            "write": "acknowledged" if observed_error is None else "interrupted",
            "member": member,
            "injected_faults": 1,
        }
        if name == "facade" and gzipx.GzipFile is not gzip.GzipFile:
            assert observed_error is fault
        # Discard the interrupted member explicitly; only an independent full
        # operation can establish the writer's recovery contract.
        sink.seek(0)
        sink.truncate()
        with stream_type(fileobj=sink, mode="wb", mtime=0) as recovered:
            assert recovered.write(PAYLOAD) == len(PAYLOAD)
        assert gzip.decompress(sink.getvalue()) == PAYLOAD
        observations[name]["recovered_bytes"] = len(PAYLOAD)
        _report(record_stream, sink)
    if gzipx.GzipFile is gzip.GzipFile:
        assert observations["facade"] == observations["stdlib"]
    chaos_journal.record("compressed-eintr-boundary", observations=observations)


@pytest.mark.parametrize("make_error", _ERRORS)
def test_gzip_buffered_short_write_fault_keeps_progress_and_restarts_member(
    make_error, record_stream
) -> None:
    sink = BytesStream(write_limit=17)
    buffered = io.BufferedWriter(sink, buffer_size=257)
    handle = gzipx.GzipFile(fileobj=buffered, mode="wb", mtime=0)
    fault = make_error()
    try:
        assert handle.write(PAYLOAD[:1000]) == 1000
        # Flush dispatches real compressed bytes through the standard buffering
        # adapter; inject after one successful raw write to prove partial I/O.
        sink.schedule.arm("write", fault, at=3)
        if isinstance(fault, InterruptedError):
            handle.flush()
        else:
            with pytest.raises(type(fault)) as caught:
                handle.flush()
            assert caught.value is fault
        assert len(sink.getvalue()) >= 27
        assert not sink.closed
        handle.flush()
        handle.close()
        buffered.flush()
        if isinstance(fault, InterruptedError):
            assert gzip.decompress(sink.getvalue()) == PAYLOAD[:1000]
        # An exception after a partial compressed write has no transactional
        # rollback promise. Recovery is a fresh complete member, as it is for
        # stdlib gzip. Keep the caller's stream and buffering adapter alive.
        buffered.seek(0)
        buffered.truncate()
        with gzipx.GzipFile(fileobj=buffered, mode="wb", mtime=0) as recovered:
            assert recovered.write(PAYLOAD[:1000]) == 1000
        buffered.flush()
        assert gzip.decompress(sink.getvalue()) == PAYLOAD[:1000]
        assert not sink.closed
        _report(record_stream, sink)
    finally:
        handle.close()
        buffered.detach()
