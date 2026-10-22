"""Streaming resource probes with incremental fixtures and independent oracles.

The resource worker imports this module before tracing. ``eager`` is a deliberate
negative control: it retains every row while preserving the same stream output.
"""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import struct
import tempfile
import tracemalloc
from typing import TYPE_CHECKING, BinaryIO
import wave
import zlib

from unstd.audio import wavx
from unstd.serde import ndjson


if TYPE_CHECKING:
    from collections.abc import Iterator


def _record(index: int) -> dict[str, object]:
    return {"index": index, "payload": f"{index:08x}:" + "x" * 247}


def _start_peak() -> int:
    baseline = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    return baseline


def _peak_bytes(baseline: int) -> int:
    return max(0, tracemalloc.get_traced_memory()[1] - baseline)


def ndjson_read(size: int, *, eager: bool = False) -> dict[str, object]:
    """Consume independent JSON lines without retaining the corpus."""
    produced = input_bytes = 0

    def lines() -> Iterator[bytes]:
        nonlocal produced, input_bytes
        for index in range(size):
            raw = (json.dumps(_record(index)) + "\n").encode()
            produced += 1
            input_bytes += len(raw)
            yield raw

    retained = []
    baseline = _start_peak()
    stream = ndjson.iter_loads(lines())
    before_first = produced
    first = next(stream)
    after_first = produced
    count = checksum = expected_checksum = 0
    valid = True
    for value in itertools.chain((first,), stream):
        expected = _record(count)
        valid = valid and value == expected
        checksum = zlib.crc32(value["payload"].encode(), checksum)
        expected_checksum = zlib.crc32(expected["payload"].encode(), expected_checksum)
        count += 1
        if eager:
            retained.append(value)
    return {
        "peak_bytes": _peak_bytes(baseline),
        "count": count,
        "checksum": checksum,
        "expected_checksum": expected_checksum,
        "valid": valid,
        "bytes_processed": input_bytes,
        "consumed_before_first": before_first,
        "consumed_after_first": after_first,
        "consumed": produced,
    }


class _CountingSink:
    """Forward bytes to a real file while observing source consumption."""

    def __init__(self, stream: BinaryIO, produced: list[int]) -> None:
        self.stream = stream
        self.produced = produced
        self.bytes = self.lines = self.max_ahead = 0
        self.first_write_consumed: int | None = None

    def write(self, data: bytes, /) -> int:
        if self.first_write_consumed is None:
            self.first_write_consumed = self.produced[0]
        self.max_ahead = max(self.max_ahead, self.produced[0] - self.lines)
        self.bytes += len(data)
        self.lines += data.count(b"\n")
        return self.stream.write(data)


def ndjson_write(size: int, *, eager: bool = False) -> dict[str, object]:
    """Write incremental rows to disk, then verify with the stdlib decoder."""
    produced = [0]
    retained = []

    def rows() -> Iterator[dict[str, object]]:
        for index in range(size):
            row = _record(index)
            produced[0] += 1
            if eager:
                retained.append(row)
            yield row

    with tempfile.TemporaryFile("w+b") as stream:
        sink = _CountingSink(stream, produced)
        baseline = _start_peak()
        written = ndjson.dump(rows(), sink)
        peak = _peak_bytes(baseline)
        stream.seek(0)
        count = checksum = expected_checksum = 0
        valid = True
        for raw in stream:
            value = json.loads(raw)
            expected = _record(count)
            valid = valid and raw.endswith(b"\n") and value == expected
            checksum = zlib.crc32(value["payload"].encode(), checksum)
            expected_checksum = zlib.crc32(
                expected["payload"].encode(), expected_checksum
            )
            count += 1
    return {
        "peak_bytes": peak,
        "count": count,
        "written": written,
        "checksum": checksum,
        "expected_checksum": expected_checksum,
        "valid": valid,
        "bytes_processed": sink.bytes,
        "first_write_consumed": sink.first_write_consumed,
        "max_ahead": sink.max_ahead,
        "consumed": produced[0],
    }


_PCM = (-32768, -8192, 12345, 32767)


def _file_descriptors(path: Path) -> int | None:
    """Count actual OS descriptors for this inode on Linux and macOS."""
    directory = Path("/proc/self/fd")
    if not directory.is_dir():
        directory = Path("/dev/fd")
    if not directory.is_dir():
        return None
    target = path.stat()
    count = 0
    for entry in list(directory.iterdir()):
        try:
            opened = os.fstat(int(entry.name))
        except (OSError, ValueError):
            continue  # The listing's own descriptor may already be closed.
        count += (opened.st_dev, opened.st_ino) == (target.st_dev, target.st_ino)
    return count


def _pcm_checksum(size: int) -> int:
    cycles, remainder = divmod(size, len(_PCM))
    return sum(
        value * (4 * cycles * (cycles - 1) // 2 + (offset + 1) * cycles)
        for offset, value in enumerate(_PCM)
    ) + sum(_PCM[offset] * (cycles * 4 + offset + 1) for offset in range(remainder))


def wav_blocks(size: int) -> dict[str, object]:
    """Decode a disk WAV in bounded chunks and observe early-close ownership."""
    blocksize = 1024
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "stream.wav"
        pcm = struct.pack("<4h", *_PCM) * (blocksize // 4)
        with wave.open(str(path), "wb") as writer:
            writer.setparams((1, 2, 16000, size, "NONE", "not compressed"))
            for offset in range(0, size, blocksize):
                writer.writeframesraw(pcm[: min(blocksize, size - offset) * 2])

        baseline = _start_peak()
        frames = checksum = max_block_frames = 0
        valid = True
        for block in wavx.blocks(str(path), blocksize, dtype="int16"):
            max_block_frames = max(max_block_frames, len(block))
            for sample in block:
                value = int(sample)
                valid = valid and value == _PCM[frames % 4]
                checksum += (frames + 1) * value
                frames += 1
        peak = _peak_bytes(baseline)

        descriptors_before = _file_descriptors(path)
        early = wavx.blocks(str(path), blocksize, dtype="int16")
        early_frames = len(next(early))
        descriptors_open = _file_descriptors(path)
        early.close()
        descriptors_closed = _file_descriptors(path)
        with path.open("rb") as caller:
            borrowed = wavx.blocks(caller, blocksize, dtype="int16")
            next(borrowed)
            borrowed.close()
            caller_open = not caller.closed
            caller_usable = os.fstat(caller.fileno()).st_ino == path.stat().st_ino

    return {
        "peak_bytes": peak,
        "frames": frames,
        "checksum": checksum,
        "expected_checksum": _pcm_checksum(size),
        "valid": valid,
        "max_block_frames": max_block_frames,
        "blocksize": blocksize,
        "bytes_processed": size * 2,
        "early_frames": early_frames,
        "descriptors_before": descriptors_before,
        "descriptors_open": descriptors_open,
        "descriptors_closed": descriptors_closed,
        "caller_open": caller_open,
        "caller_usable": caller_usable,
    }
