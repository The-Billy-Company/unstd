"""Read benchmarks must retain output checks and actual source provenance."""

from __future__ import annotations

from argparse import Namespace
import gzip
import sys
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
    for operation in ("decompress", "lines", "upload", "bounded"):
        case = gzip_reads._Case(operation, raw, packed, operation, limit=11)
        actual = gzip_reads._operation(gzip, case)()
        assert actual == (raw[:12] if operation == "bounded" else raw)
