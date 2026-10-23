"""Read benchmarks must retain output checks and actual source provenance."""

from __future__ import annotations

from argparse import Namespace
import gzip
import io
import sys
import tracemalloc
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from bench import gzip_reads


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
