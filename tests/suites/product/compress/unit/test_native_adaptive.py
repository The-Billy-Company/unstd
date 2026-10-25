"""Mac ARM structural selection and unchanged bounded native stream histories."""

from __future__ import annotations

import gc
import json
import platform
import subprocess
import sys
import textwrap
import zlib

import pytest

from tests.support.environment.backends import require_backend


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine().lower() not in {"arm64", "aarch64"},
    reason="adaptive native inflation requires Darwin ARM64",
)


@pytest.fixture(scope="module")
def adaptive_native():
    native = require_backend("unstd.compress._gzip_native")
    provider = require_backend("zlib_ng.zlib_ng")
    assert native._configure_ng(provider.__file__), "native provider ABI unavailable"
    assert native._ng_runtime_version() == provider.ZLIBNG_RUNTIME_VERSION
    assert native.Inflater()._selected_backend == "pending"
    return native


def test_adaptive_heap_inflater_exposes_its_type_to_cycle_collection(adaptive_native):
    decoder = adaptive_native.Inflater()
    assert gc.is_tracked(decoder)
    assert type(decoder) in gc.get_referents(decoder)


class _Bits:
    def __init__(self):
        self.bits = []

    def little(self, value, count):
        self.bits.extend((value >> shift) & 1 for shift in range(count))

    def code(self, value, count):
        self.bits.extend((value >> shift) & 1 for shift in range(count - 1, -1, -1))

    def finish(self):
        return bytes(
            sum(bit << shift for shift, bit in enumerate(self.bits[start : start + 8]))
            for start in range(0, len(self.bits), 8)
        )


def _dynamic(
    *,
    final=True,
    distances=(1, 1),
    malformed=None,
    literals=1,
    long_matches=64,
    short_matches=0,
    short_length=3,
    alternating_distances=False,
):
    """Independent RFC1951 wire trace: one literal, distance-one long matches, EOB.

    The default literal tree is {a:10, END:11, length258:0}. The precode is
    thirteen four-bit codes followed by six five-bit codes. There is no parser
    or copy of the native classifier in this fixture builder.
    """
    bits = _Bits()
    bits.little(int(final), 1)
    bits.little(2, 2)
    bits.little(29, 5)
    bits.little(len(distances) - 1, 5)
    bits.little(15, 4)
    precode = [4] * 13 + [5] * 6
    if malformed == "empty-precode":
        precode = [0] * 19
    elif malformed == "oversubscribed-precode":
        precode = [1] * 19
    for symbol in (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15):
        bits.little(precode[symbol], 3)
    if malformed in {"empty-precode", "oversubscribed-precode"}:
        return bits.finish() + bytes(32), b"", len(bits.bits)

    def encoded(symbol):
        bits.code(symbol if symbol < 13 else symbol + 13, 4 if symbol < 13 else 5)

    if malformed == "repeat-without-previous":
        encoded(16)
        bits.little(0, 2)
        return bits.finish() + bytes(32), b"", len(bits.bits)
    if malformed == "repeat-overflow":
        for _ in range(3):
            encoded(18)
            bits.little(127, 7)
        return bits.finish() + bytes(32), b"", len(bits.bits)

    lengths = [0] * 286
    lengths[97] = lengths[256] = 2
    lengths[285] = 1
    if short_matches:
        lengths[256] = 3
        lengths[257 if short_length == 3 else 284] = 3
    if malformed == "missing-end":
        lengths[256] = 0
    elif malformed == "oversubscribed-literals":
        lengths[97] = lengths[256] = 1
    elif malformed == "incomplete-literals":
        lengths[97] = lengths[256] = 3
    for length in (*lengths, *distances):
        encoded(length)
    header_bits = len(bits.bits)
    for _ in range(literals):
        bits.code(2, 2)  # literal a
    for index in range(long_matches):
        bits.code(0, 1)  # length258, no extra bits
        # The two one-bit distance codes encode distances1and2, respectively.
        bits.code(index % 2 if alternating_distances else 0, distances[0])
    for _ in range(short_matches):
        bits.code(7, 3)  # code257 (length3) or284 (base227 plus5 extra bits)
        if short_length != 3:
            bits.little(short_length - 227, 5)
        bits.code(0, distances[0])
    bits.code(6 if short_matches else 3, 3 if short_matches else 2)  # END
    if not final:
        bits.little(1, 1)
        bits.little(0, 2)  # final stored empty block
        while len(bits.bits) % 8:
            bits.little(0, 1)
        bits.little(0, 16)
        bits.little(65535, 16)
    produced = literals + 258 * long_matches + short_length * short_matches
    return bits.finish(), b"a" * produced, header_bits


def _drain(decoder, incoming, *, limit=4096, legacy=False):
    pieces = []
    for _ in range(1000):
        piece = decoder.decompress(incoming, limit)
        assert len(piece) <= limit
        pieces.append(piece)
        if decoder.eof:
            return b"".join(pieces)
        assert piece or decoder.unconsumed_tail or not decoder.needs_input
        incoming = decoder.unconsumed_tail if legacy else b""
    pytest.fail("complete input did not reach EOF within its bounded output")


@pytest.mark.parametrize("period", range(1, 17))
def test_short_distance_periods_select_native_ng_and_decode_every_byte(
    adaptive_native, period
):
    pattern = bytes(range(period))
    payload = (pattern * (65536 // period + 1))[:65536]
    packed = zlib.compress(payload, wbits=-15)
    decoder = adaptive_native.Inflater()
    assert _drain(decoder, packed) == payload
    assert decoder._selected_backend == "ng"


@pytest.mark.parametrize(("budget", "expected"), [(4095, "apple"), (4096, "ng")])
def test_first_actual_output_budget_selects_once(adaptive_native, budget, expected):
    packed, payload, _ = _dynamic()
    assert zlib.decompress(packed, -15) == payload
    decoder = adaptive_native.Inflater()
    first = decoder.decompress(packed, budget)
    assert first == payload[:budget]
    assert decoder._selected_backend == expected
    assert first + _drain(decoder, b"", limit=131072) == payload
    assert decoder._selected_backend == expected


def test_default_unlimited_budget_can_select_ng(adaptive_native):
    packed, payload, _ = _dynamic()
    decoder = adaptive_native.Inflater()
    assert decoder.decompress(packed) == payload
    assert decoder._selected_backend == "ng"


@pytest.mark.parametrize("empty_call", ["input", "zero-output"])
def test_empty_work_defers_selection_until_first_actual_decode(
    adaptive_native, empty_call
):
    packed, payload, _ = _dynamic()
    decoder = adaptive_native.Inflater()
    if empty_call == "input":
        assert decoder.decompress(b"", 4096) == b""
        incoming = packed
    else:
        assert decoder.decompress(packed, 0) == b""
        incoming = b""
    assert decoder._selected_backend == "pending"
    assert _drain(decoder, incoming) == payload
    assert decoder._selected_backend == "ng"


@pytest.mark.parametrize("kind", ["json", "period17", "nonfinal", "distance9"])
def test_ordinary_and_unsupported_valid_streams_stay_on_apple(adaptive_native, kind):
    if kind == "json":
        payload = json.dumps(
            [
                {"id": i, "name": f"source row {i}", "amount": i / 10}
                for i in range(2000)
            ]
        ).encode()
        packed = zlib.compress(payload, wbits=-15)
    elif kind == "period17":
        payload = bytes(range(17)) * 4096
        packed = zlib.compress(payload, wbits=-15)
    else:
        packed, payload, _ = _dynamic(
            final=kind != "nonfinal",
            distances=(3,) * 7 + (4,) * 2 if kind == "distance9" else (1, 1),
        )
    assert zlib.decompress(packed, -15) == payload
    decoder = adaptive_native.Inflater()
    assert _drain(decoder, packed) == payload
    assert decoder._selected_backend == "apple"


def test_every_truncated_header_stays_on_apple_for_the_rest_of_the_stream(
    adaptive_native,
):
    packed, payload, header_bits = _dynamic()
    assert zlib.decompress(packed, -15) == payload
    for length in range(1, (header_bits + 7) // 8):
        decoder = adaptive_native.Inflater()
        assert decoder.decompress(packed[:length], 4096) == b""
        assert decoder._selected_backend == "apple"
        assert _drain(decoder, packed[length:]) == payload
        assert decoder._selected_backend == "apple"


@pytest.mark.parametrize(
    "damage",
    [
        "empty-precode",
        "oversubscribed-precode",
        "repeat-without-previous",
        "repeat-overflow",
        "missing-end",
        "oversubscribed-literals",
        "incomplete-literals",
    ],
)
def test_malformed_headers_fail_closed_with_the_original_codec_error(
    adaptive_native, damage
):
    packed, _, _ = _dynamic(malformed=damage)
    reference = zlib.decompressobj(-15)
    decoder = adaptive_native.Inflater()
    try:
        expected = reference.decompress(packed, 4096)
    except zlib.error:
        with pytest.raises(zlib.error) as caught:
            decoder.decompress(packed, 4096)
        assert type(caught.value) is zlib.error
    else:
        # Some Apple zlib versions wait for further bits for an empty precode.
        # Fail-closed routing must preserve that incomplete-state behavior too.
        assert decoder.decompress(packed, 4096) == expected
        assert decoder.eof == reference.eof
        assert decoder.unused_data == reference.unused_data
    assert decoder._selected_backend == "apple"


@pytest.mark.parametrize("legacy", [False, True])
def test_adaptive_bounded_eof_and_tail_handoff_match_existing_contract(
    adaptive_native, legacy
):
    packed, payload, _ = _dynamic()
    trailer = b"following member"
    decoder = adaptive_native.Inflater(legacy=legacy)
    first = decoder.decompress(packed + trailer, 4096)
    assert first == payload[:4096]
    assert not decoder.eof
    assert not decoder.needs_input
    assert decoder._selected_backend == "ng"
    if legacy:
        assert decoder.unconsumed_tail
        assert (packed + trailer).endswith(decoder.unconsumed_tail)
    incoming = decoder.unconsumed_tail if legacy else b""
    assert first + _drain(decoder, incoming, legacy=legacy) == payload
    assert decoder.eof
    assert not decoder.needs_input
    assert decoder.unused_data == trailer
    assert decoder._selected_backend == "ng"
    with pytest.raises(EOFError):
        decoder.decompress(b"", 4096)
    assert decoder.unused_data == trailer
    decoder.__init__(legacy=legacy)
    assert decoder._selected_backend == "pending"
    assert not decoder.eof
    assert decoder.needs_input
    assert decoder.unused_data == b""
    assert decoder.decompress(packed, 4095) == payload[:4095]
    assert decoder._selected_backend == "apple"


def test_adaptive_input_snapshots_mutable_bytes_before_retaining_them(adaptive_native):
    packed, payload, _ = _dynamic()
    source = bytearray(packed)
    decoder = adaptive_native.Inflater()
    first = decoder.decompress(source, 4096)
    assert decoder._selected_backend == "ng"
    source[:] = b"\xff" * len(source)
    assert first + _drain(decoder, b"") == payload


def test_direct_output_selection_keeps_budget_and_aliased_input_snapshot(
    adaptive_native,
):
    packed, payload, _ = _dynamic()
    storage = bytearray(packed + bytes(4096 - len(packed)) + b"!" * 64)
    source = memoryview(storage)[: len(packed)]
    target = memoryview(storage)[:4096]
    decoder = adaptive_native.Inflater()
    result = decoder.decompress_into(source, target)
    assert decoder._selected_backend == "ng"
    assert bytes(result) == payload[:4096]
    assert storage[4096:] == b"!" * 64
    result.release()
    source.release()
    target.release()
    assert payload[:4096] + _drain(decoder, b"") == payload


@pytest.mark.parametrize("callback", ["index", "acquire", "release", "target"])
def test_exporter_callbacks_can_reset_a_selected_ng_decoder_safely(
    adaptive_native, callback
):
    script = f"MODULE = {adaptive_native.__name__!r}\nHOOK = {callback!r}\n"
    script += textwrap.dedent(
        """\
        import importlib
        import zlib
        from zlib_ng import zlib_ng
        native = importlib.import_module(MODULE)
        assert native._configure_ng(zlib_ng.__file__)
        decoder = native.Inflater()
        old = zlib.compress(b'a' * 65536, wbits=-15)
        assert decoder.decompress(old, 4096) == b'a' * 4096
        assert decoder._selected_backend == 'ng'
        payload = b'b' * 65536
        packed = zlib.compress(payload, wbits=-15)
        calls = []
        def reset():
            calls.append(HOOK)
            decoder.__init__()
            assert decoder._selected_backend == 'pending'
        class Source:
            def __buffer__(self, flags):
                if HOOK == 'acquire': reset()
                return memoryview(packed)
            def __release_buffer__(self, view):
                if HOOK == 'release': reset()
        class Limit:
            def __index__(self):
                reset()
                return 4096
        storage = bytearray(4096)
        class Target:
            def __buffer__(self, flags):
                reset()
                return memoryview(storage)
        if HOOK == 'target':
            view = decoder.decompress_into(packed, Target())
            first = bytes(view)
            view.release()
        else:
            first = decoder.decompress(
                Source() if HOOK in ('acquire', 'release') else packed,
                Limit() if HOOK == 'index' else 4096,
            )
        assert calls == [HOOK]
        assert first == payload[:4096]
        assert decoder._selected_backend == 'ng'
        parts = [first]
        while not decoder.eof:
            piece = decoder.decompress(b'', 4096)
            assert len(piece) <= 4096
            parts.append(piece)
        assert b''.join(parts) == payload
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed bounded native callback regression
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_unconfigured_decoder_stays_on_apple_until_explicit_reset(adaptive_native):
    script = f"MODULE = {adaptive_native.__name__!r}\n" + textwrap.dedent(
        """\
        import importlib
        import zlib
        native = importlib.import_module(MODULE)
        assert native._ng_runtime_version() is None
        assert not native._configure_ng('/unstd-not-an-already-loaded-provider')
        decoder = native.Inflater()
        assert decoder._selected_backend == 'apple'
        from zlib_ng import zlib_ng
        assert native._configure_ng(zlib_ng.__file__)
        payload = b'a' * 65536
        packed = zlib.compress(payload, wbits=-15)
        assert decoder.decompress(packed, 4096) == payload[:4096]
        assert decoder._selected_backend == 'apple'
        decoder.__init__()
        assert decoder._selected_backend == 'pending'
        assert decoder.decompress(packed, 4096) == payload[:4096]
        assert decoder._selected_backend == 'ng'
        try:
            decoder._selected_backend = 'apple'
        except AttributeError:
            pass
        else:
            raise AssertionError('backend proof was writable')
        """
    )
    result = subprocess.run(  # noqa: S603 — isolated native binding/ownership trace
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"literals": 512, "long_matches": 0}, "apple"),
        ({"long_matches": 0, "short_matches": 512}, "apple"),
        ({"long_matches": 63}, "apple"),
        ({"literals": 2, "alternating_distances": True}, "apple"),
        ({"literals": 32}, "ng"),
        ({"literals": 33}, "apple"),
        ({"short_matches": 9}, "apple"),
        ({"short_matches": 7, "short_length": 257}, "ng"),
        ({"short_matches": 8, "short_length": 257}, "apple"),
        ({"long_matches": 4095}, "ng"),
        ({"long_matches": 4096}, "apple"),
    ],
)
def test_actual_body_usage_and_caps_control_selection(
    adaptive_native, options, expected
):
    # Every header still declares code285 with its shortest code. The payload,
    # independently checked by stdlib, establishes whether that declaration is
    # used often enough and stays inside the whole-block probe's limits.
    packed, payload, _ = _dynamic(**options)
    assert zlib.decompress(packed, -15) == payload
    decoder = adaptive_native.Inflater()
    assert _drain(decoder, packed) == payload
    assert decoder._selected_backend == expected


def test_partial_body_cannot_select_ng_after_later_input_arrives(adaptive_native):
    packed, payload, header_bits = _dynamic()
    start = (header_bits + 7) // 8
    for length in (start, start + (len(packed) - start) // 2, len(packed) - 1):
        decoder = adaptive_native.Inflater()
        first = decoder.decompress(packed[:length], 4096)
        assert decoder._selected_backend == "apple"
        assert first + _drain(decoder, packed[length:]) == payload
        assert decoder._selected_backend == "apple"


@pytest.mark.parametrize(("size", "expected"), [(8192, "ng"), (8193, "apple")])
def test_whole_probe_input_cap_preserves_unconsumed_suffix(
    adaptive_native, size, expected
):
    packed, payload, _ = _dynamic()
    suffix = b"!" * (size - len(packed))
    decoder = adaptive_native.Inflater()
    assert _drain(decoder, packed + suffix) == payload
    assert decoder._selected_backend == expected
    assert decoder.unused_data == suffix


def test_invalid_distance_before_any_literal_fails_closed(adaptive_native):
    packed, _, _ = _dynamic(literals=0)
    with pytest.raises(zlib.error):
        zlib.decompressobj(-15).decompress(packed, 4096)
    decoder = adaptive_native.Inflater()
    with pytest.raises(zlib.error):
        decoder.decompress(packed, 4096)
    assert decoder._selected_backend == "apple"
