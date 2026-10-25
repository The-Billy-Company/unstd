"""Read benchmarks must retain output checks and actual source provenance."""

from __future__ import annotations

from argparse import Namespace
import gzip
import io
import sys
import tracemalloc
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from bench.profiles import gzip_reads


if TYPE_CHECKING:
    from pathlib import Path


def test_corrupted_decoder_output_is_rejected_before_timing(monkeypatch) -> None:
    raw = b"saved source bytes"
    case = gzip_reads._Case("saved", raw, gzip.compress(raw, mtime=0))
    monkeypatch.setattr(gzip_reads, "_cases", lambda _fixtures: [case])
    monkeypatch.setattr(
        gzip_reads, "_operation", lambda _codec, _case: lambda: b"changed source bytes"
    )
    with pytest.raises(AssertionError, match="decoded bytes differ"):
        gzip_reads._worker(Namespace(source=None, stdlib=True, fixture=[]))


def test_wrong_imported_source_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(gzip_reads.importlib, "import_module", lambda _name: gzip)
    with pytest.raises(RuntimeError, match="requested source was not imported"):
        gzip_reads._worker(Namespace(source=tmp_path, stdlib=False, fixture=[]))


def test_stream_workloads_preserve_bounded_output_and_rewind() -> None:
    raw = b"first\nsecond\n" * 100
    packed = gzip.compress(raw[:40], mtime=0) + gzip.compress(raw[40:], mtime=0)
    for operation in ("decompress", "lines", "bounded"):
        case = gzip_reads._Case(operation, raw, packed, operation, limit=11)
        actual = gzip_reads._operation(gzip, case)()
        assert actual == (raw[:12] if operation == "bounded" else raw)


def test_upload_hashes_and_replays_without_retaining_expanded_outputs() -> None:
    blake3 = pytest.importorskip("blake3").blake3
    raw = bytes(range(256)) * 32768
    case = gzip_reads._Case("upload", raw, gzip.compress(raw, mtime=0), "upload")
    operation = gzip_reads._operation(gzip, case)
    expected = blake3(raw).digest(), len(raw), len(raw)
    tracemalloc.start()
    try:
        assert operation() == expected
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024, "upload retained complete expanded outputs"
    gzip_reads._validate_upload_bytes(gzip, case)


def test_upload_validation_rejects_same_length_corruption_after_rewind() -> None:
    blake3 = pytest.importorskip("blake3").blake3
    raw = b"saved source bytes"
    case = gzip_reads._Case("upload", raw, b"packed input", "upload")

    class ChangedReplay(io.BytesIO):
        def seek(self, offset, whence=0):
            if offset == 0 and whence == 0:
                self.getbuffer()[0] ^= 1
            return super().seek(offset, whence)

    codec = SimpleNamespace(GzipFile=lambda **_options: ChangedReplay(raw))
    # The production-style timed summary cannot detect a same-length change
    # on its unhashed second pass; qualification must independently check it.
    assert gzip_reads._operation(codec, case)() == (
        blake3(raw).digest(),
        len(raw),
        len(raw),
    )
    with pytest.raises(AssertionError, match="decoded bytes differ"):
        gzip_reads._validate_upload_bytes(codec, case)


def test_counted_stream_checks_bytes_and_does_not_retain_expanded_output() -> None:
    raw = bytes(range(16)) * (512 * 1024)
    case = gzip_reads._Case(
        "counted", raw, gzip.compress(raw, mtime=0), "stream-count", chunk_bytes=8192
    )
    tracemalloc.start()
    try:
        assert gzip_reads._operation(gzip, case)() == len(raw)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
    gzip_reads._validate_stream_bytes(gzip, case)


def test_counted_stream_oracle_rejects_same_length_corruption() -> None:
    case = gzip_reads._Case("counted", b"original", b"packed", "stream-count")
    codec = SimpleNamespace(GzipFile=lambda **_options: io.BytesIO(b"changed!"))
    assert gzip_reads._operation(codec, case)() == len(case.raw)
    with pytest.raises(AssertionError, match="decoded bytes differ"):
        gzip_reads._validate_stream_bytes(codec, case)


@pytest.mark.parametrize("fails", [False, True])
def test_backend_proof_restores_the_factory_outside_timing(monkeypatch, fails):
    class Stream:
        _selected_backend = "pending"

    reader = ModuleType("unstd.compress._reader")
    reader._factory = Stream
    monkeypatch.setitem(sys.modules, reader.__name__, reader)

    def operation():
        stream = reader._factory()
        stream._selected_backend = "ng"
        if fails:
            message = "source callback failed"
            raise OSError(message)

    codec = ModuleType("codec_probe")
    if fails:
        with pytest.raises(OSError, match="source callback failed"):
            gzip_reads._native_backends(codec, operation)
    else:
        assert gzip_reads._native_backends(codec, operation) == {"ng": 1}
    assert reader._factory is Stream


@pytest.mark.parametrize(("executed", "accepted"), [(False, True), (True, False)])
def test_native_requirement_rejects_fallback_or_refused_buffers(
    executed: bool, accepted: bool
) -> None:
    with pytest.raises(AssertionError, match="accelerated decoder did not accept"):
        gzip_reads._require_native(
            [
                {
                    "name": "fixture",
                    "native_decode_executed": executed,
                    "native_buffer_accepts_fixture": accepted,
                }
            ]
        )
