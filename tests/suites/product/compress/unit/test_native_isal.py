"""Linux SIMD inflation keeps raw input tails and output budgets observable."""

from __future__ import annotations

import importlib.util
from random import Random
import struct
import zlib

import pytest


@pytest.fixture(scope="module")
def unbound_native():
    native = pytest.importorskip("unstd.compress._gzip_native")
    if not getattr(native, "ISAL_STREAMING", False):
        pytest.skip("private ISA-L route is unavailable on this build or CPU")
    specification = importlib.util.spec_from_file_location(
        "_unstd_isal_unbound._gzip_native", native.__file__
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    assert module._ng_runtime_version() is None
    return module


@pytest.fixture(params=["provider", "unbound"])
def codecs(request, unbound_native):
    native = (
        pytest.importorskip("unstd.compress._gzip_native")
        if request.param == "provider"
        else unbound_native
    )
    ng = pytest.importorskip("zlib_ng.zlib_ng")
    if request.param == "provider":
        # Source-built providers may intentionally hide their static-library
        # symbols. Keep the ABI proof strict; ISA-L also works with bundled
        # zlib as its untouched fallback when no NG API can be bound.
        bound = native._configure_ng(ng.__file__)
        assert bound is (native._ng_runtime_version() is not None)
    else:
        assert native._ng_runtime_version() is None
    return native, ng


def _prefix(ng, provider="native"):
    rng = Random(59231)  # noqa: S311 — reproducible compressed body
    raw = rng.randbytes(24576).translate(bytes(i & 31 for i in range(256)))
    encoder = (ng if provider == "native" else zlib).compressobj(wbits=-15)
    wire = encoder.compress(raw) + encoder.flush(ng.Z_SYNC_FLUSH)
    assert len(wire) >= 8192
    assert wire[0] & 6 == 4
    assert wire[1] & 31 >= 8
    return raw, wire


def _history(factory, wire, budgets, error_type):
    decoder = factory(wbits=-15)
    records = []
    for index in range(40000):
        incoming = wire if index == 0 else b""
        try:
            result = decoder.decompress(incoming, budgets[min(index, len(budgets) - 1)])
        except error_type as error:
            records.append(("error", str(error)))
            break
        records.append((result, decoder.needs_input, decoder.eof, decoder.unused_data))
        if decoder.eof or (not result and decoder.needs_input):
            break
    else:
        pytest.fail("raw inflation did not reach an input or EOF boundary")
    return records, decoder


@pytest.mark.parametrize("provider", ["stdlib", "native"])
@pytest.mark.parametrize(
    "kind", ["complete", "partial-header", "partial-symbol", "invalid-block"]
)
@pytest.mark.parametrize("budgets", [(131072,), (24575, 1), (24576, 7), (24560, 1)])
def test_admitted_dynamic_stream_preserves_tail_history(
    codecs, provider, kind, budgets
):
    native, ng = codecs
    raw, prefix = _prefix(ng, provider)
    tail = bytes([1]) + struct.pack("<HH", 8192, 8192 ^ 65535) + b"x" * 8192
    wire = prefix + tail
    if kind == "complete":
        wire += b"later member bytes"
    elif kind == "partial-header":
        wire = prefix + b"\x01\x00\x00\xff"
    elif kind == "partial-symbol":
        wire = wire[: len(wire) // 2]
    else:
        wire = prefix + b"\x07"
    expected, _ = _history(ng._ZlibDecompressor, wire, budgets, ng.error)
    actual, decoder = _history(native.Inflater, wire, budgets, zlib.error)
    assert decoder._selected_backend == "isal"
    assert actual == expected
    if kind == "complete":
        assert b"".join(record[0] for record in actual) == raw + b"x" * 8192


def test_data_error_remains_latched_without_decoding_later_input(codecs):
    native, ng = codecs
    raw, prefix = _prefix(ng)
    decoder = native.Inflater()
    wire = prefix + b"\x07"
    for incoming in (wire, b"", b"unrelated later input"):
        with pytest.raises(zlib.error, match="invalid block type"):
            decoder.decompress(incoming, 131072)
    decoder.__init__()
    assert decoder.decompress(prefix + b"\x01\x00\x00\xff\xff", 131072) == raw
    assert decoder.eof


def test_unbound_short_stream_keeps_bundled_zlib_and_native_tail_state(unbound_native):
    assert not unbound_native._configure_ng("/unstd-not-an-already-loaded-provider")
    payload = b"small raw stream" * 16
    packed = zlib.compress(payload, wbits=-15)
    decoder = unbound_native.Inflater()
    assert decoder._selected_backend == "pending"
    assert decoder.decompress(packed + b"following member", 16384) == payload
    assert decoder._selected_backend == "zlib"
    assert decoder.eof
    assert decoder.unused_data == b"following member"
    assert unbound_native._ng_runtime_version() is None
