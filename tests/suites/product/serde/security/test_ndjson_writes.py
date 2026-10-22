"""NDJSON reports a record only after its complete line reaches the sink.

Raw file writes may accept a prefix or return None when a nonblocking sink is
full. Retrying the remainder must preserve UTF-8 and record boundaries, and a
failed write must stop before the next input record is consumed.
"""

from __future__ import annotations

import codecs
import io
import json
import os
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from unstd.serde import ndjson


if TYPE_CHECKING:
    from pathlib import Path


def test_standard_codec_filter_can_acknowledge_complete_writes_with_none() -> None:
    output = io.BytesIO()
    sink = codecs.getwriter("hex_codec")(output)
    rows = [{"event": "first"}, {"event": "second"}]
    assert not isinstance(sink, io.IOBase)
    assert ndjson.dump(rows, sink) == 2
    actual = bytes.fromhex(output.getvalue().decode("ascii"))
    assert [json.loads(line) for line in actual.splitlines()] == rows
    assert not output.closed


def test_generic_sink_numeric_short_writes_still_finish_each_record() -> None:
    output = io.BytesIO()

    class PrefixSink:
        def write(self, data):
            return output.write(data[:3])

    rows = [{"event": "first"}, {"event": "second"}]
    assert ndjson.dump(rows, PrefixSink()) == 2
    assert [json.loads(line) for line in output.getvalue().splitlines()] == rows


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_short_writes_finish_the_record_before_consuming_the_next(
    tmp_path: Path, binary: bool, typed: bool
) -> None:
    rows = [{"event": "café🪵"}, {"event": "deleted"}]
    expected = [
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        for row in rows
    ]
    codec = None
    if typed:
        require_backend("msgspec")
        from unstd.serde.structs import Codec

        codec = Codec(dict[str, str])
    path = tmp_path / "events.jsonl"
    options = {} if binary else {"encoding": "utf-8"}
    with path.open("wb" if binary else "w", **options) as handle:

        class ShortBinary(io.RawIOBase):
            def write(self, value):
                return handle.write(value[:3])

        class ShortText(io.TextIOBase):
            def write(self, value):
                return handle.write(value[:3])

        sink = ShortBinary() if binary else ShortText()

        def records():
            yield rows[0]
            handle.flush()
            assert path.read_bytes() == expected[0]
            yield rows[1]

        assert ndjson.dump(records(), sink, codec=codec) == 2
        assert not sink.closed
        assert not handle.closed
    assert path.read_bytes() == b"".join(expected)


@pytest.mark.parametrize("binary", [False, True])
def test_failure_after_a_partial_write_propagates_without_consuming_ahead(
    tmp_path: Path, binary: bool
) -> None:
    failure = OSError("sink disconnected")
    consumed = []
    path = tmp_path / "events.jsonl"
    options = {} if binary else {"encoding": "utf-8"}
    with path.open("wb" if binary else "w", **options) as handle:

        def write(value):
            if handle.tell():
                raise failure
            return handle.write(value[:3])

        class PartialBinary(io.RawIOBase):
            def write(self, value):
                return write(value)

        class PartialText(io.TextIOBase):
            def write(self, value):
                return write(value)

        def records():
            for value in [1, 2, 3]:
                consumed.append(value)
                yield {"value": value}

        sink = PartialBinary() if binary else PartialText()
        with pytest.raises(OSError, match="sink disconnected") as caught:
            ndjson.dump(records(), sink)
        assert caught.value is failure
        assert consumed == [1]
        assert not handle.closed
    assert path.read_bytes() == b'{"v'


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("result", [None, 0])
def test_writes_without_progress_fail_without_claiming_success(binary, result) -> None:
    consumed = []
    attempts = []

    def blocked_write(value):
        attempts.append(value)
        assert len(attempts) == 1, "retried a sink without progress"
        return result

    class BlockedBinary(io.RawIOBase):
        def write(self, value):
            return blocked_write(value)

    class BlockedText(io.TextIOBase):
        def write(self, value):
            return blocked_write(value)

    def records():
        for value in [1, 2]:
            consumed.append(value)
            yield value

    sink = BlockedBinary() if binary else BlockedText()
    with pytest.raises(BlockingIOError):
        ndjson.dump(records(), sink)
    assert consumed == [1]
    assert not sink.closed


def test_full_nonblocking_pipe_is_an_io_failure() -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.set_blocking(write_fd, False)
        for _ in range(1024):
            try:
                os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                break
        else:
            pytest.fail("could not fill the pipe within the bounded capacity")
        with io.FileIO(write_fd, "wb", closefd=False) as sink:
            with pytest.raises(BlockingIOError):
                ndjson.dump([{"event": "must not disappear"}], sink)
            assert not sink.closed
    finally:
        os.close(write_fd)
        os.close(read_fd)
