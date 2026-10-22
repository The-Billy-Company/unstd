"""The `unstd.crypto.digest` contract, refereed by published BLAKE3 and BLAKE2b answers.

Every expected digest is either the BLAKE3 authors' vendored vector, an RFC 7693
BLAKE2b value, or a metamorphic comparison stated by the module documentation.
"""

from __future__ import annotations

from array import array
import gc
import json
from pathlib import Path
import tracemalloc

import pytest

from tests.support.environment.backends import require_backend


digest = require_backend("unstd.crypto.digest")

_REFERENCE = json.loads(
    Path(__file__).with_name("blake3_reference_vectors.json").read_text()
)
_CONTEXT: str = _REFERENCE["context_string"]
_HASH = {case["input_len"]: case["hash"][:64] for case in _REFERENCE["cases"]}
_DERIVED = {case["input_len"]: case["derive_key"][:64] for case in _REFERENCE["cases"]}
_FILLER = bytes(i % 251 for i in range(max(_HASH)))


def _buffers(data: bytes) -> dict[str, object]:
    """Every u8-compatible buffer shape the serial backend documents as accepted."""
    shaped = {
        "bytes": data,
        "bytearray": bytearray(data),
        "memoryview": memoryview(data),
        "readonly-view": memoryview(bytearray(data)).toreadonly(),
        "array-B": array("B", data),
        "array-b": array("b", data),
        "char-view": memoryview(data).cast("c"),
    }
    if data and len(data) % 2 == 0:
        shaped["2d-view"] = memoryview(data).cast("B", shape=(2, len(data) // 2))
    return shaped


@pytest.mark.parametrize("length", sorted(_HASH), ids=lambda n: f"len{n}")
def test_every_accepted_buffer_type_hashes_to_the_published_vector(length: int):
    expected = _HASH[length]
    for kind, buffer in _buffers(_FILLER[:length]).items():
        assert digest.raw(buffer) == bytes.fromhex(expected), kind
        assert digest.hex(buffer) == expected, kind
        assert digest.hex(buffer, 5) == expected[:10], kind
        assert digest.derive_key(_CONTEXT, buffer).hex() == _DERIVED[length], kind
        stream = digest.Stream().update(buffer)
        assert stream.raw() == bytes.fromhex(expected), kind


@pytest.mark.parametrize(
    ("buffer", "error", "message"),
    [
        (memoryview(_FILLER[:64])[::2], BufferError, "buffer is not contiguous"),
        (
            memoryview(_FILLER[:64]).cast("I"),
            BufferError,
            "buffer contents are not compatible with u8",
        ),
        (array("I", _FILLER[:64]), BufferError, "not compatible with u8"),
        ("text", TypeError, "a bytes-like object is required, not 'str'"),
    ],
    ids=["strided", "u32-view", "u32-array", "str"],
)
def test_rejected_inputs_raise_and_leave_a_stream_unchanged(buffer, error, message):
    for call in (
        digest.raw,
        digest.hex,
        lambda data: digest.derive_key(_CONTEXT, data),
    ):
        with pytest.raises(error, match=message):
            call(buffer)
    stream = digest.Stream().update(_FILLER[:1023])
    with pytest.raises(error, match=message):
        stream.update(buffer)
    assert stream.hex() == _HASH[1023]
    assert stream.update(_FILLER[1023:1025]).hex() == _HASH[1025]


def test_reading_never_finalizes_across_every_published_boundary():
    """One stream walks 0..102400 bytes, read repeatedly at each published length."""
    stream, absorbed = digest.Stream(), 0
    for length, expected in sorted(_HASH.items()):
        assert stream.update(_FILLER[absorbed:length]) is stream
        absorbed = length
        assert stream.raw() == bytes.fromhex(expected)
        assert stream.hex() == expected
        assert stream.hex(8) == expected[:16]
        assert stream.raw() == bytes.fromhex(expected)


@pytest.mark.parametrize("length", [64, 1024, 1025, 8193])
def test_every_truncation_width_is_a_prefix_of_the_published_digest(length: int):
    expected = _HASH[length]
    stream = digest.Stream([_FILLER[:length]])
    for n in range(1, digest.DIGEST_BYTES + 1):
        assert digest.hex(_FILLER[:length], n) == expected[: 2 * n]
        assert stream.hex(n) == expected[: 2 * n]
    assert digest.hex(_FILLER[:length], digest.DIGEST_BYTES) == expected


@pytest.mark.parametrize("n", [0, -1, 33, 64])
def test_stream_truncation_bounds_reject_and_keep_the_stream_usable(n: int):
    stream = digest.Stream([_FILLER[:64]])
    with pytest.raises(
        ValueError, match=rf"^truncation width must be 1\.\.32 bytes, got {n}$"
    ):
        stream.hex(n)
    assert stream.update(_FILLER[64:65]).hex() == _HASH[65]


def test_digest_width_is_blake3_native_output():
    assert digest.DIGEST_BYTES == 32
    assert len(digest.raw(b"")) == len(digest.derive_key(_CONTEXT, b"")) == 32
    assert len(digest.Stream().raw()) == 32


@pytest.mark.parametrize(
    ("context", "message"),
    [
        (_CONTEXT.encode(), r"^'bytes' object is not an instance of 'str'$"),
        (7, r"^'int' object is not an instance of 'str'$"),
    ],
)
def test_derive_key_context_must_be_text(context: object, message: str):
    with pytest.raises(TypeError, match=message):
        digest.derive_key(context, _FILLER[:64])


def test_derive_key_accepts_a_str_subclass_context_as_its_text():
    class Context(str):
        pass

    assert digest.derive_key(Context(_CONTEXT), _FILLER[:1024]).hex() == _DERIVED[1024]


class TestLegacyBlake2b:
    """RFC 7693 Appendix A and the widely published empty-input BLAKE2b-256."""

    def test_rfc7693_abc_vector(self):
        assert digest.legacy_blake2b(b"abc", width=64) == bytes.fromhex(
            "ba80a53f981c4d0d6a2797b69f12f6e94c212f14685ac4b74b12bb6fdbffa2d1"
            "7d87c5392aab792dc252d5de4533cc9518d38aa8dbf1925ab92386edd4009923"
        )

    def test_width_is_a_parameter_not_a_truncation(self):
        assert digest.legacy_blake2b(b"", width=32) == bytes.fromhex(
            "0e5751c026e543b2e8ab2eb06099daa1d1e5df47778f7787faab45cdf12fe3a8"
        )
        full = digest.legacy_blake2b(b"", width=64)
        assert full[:32] != digest.legacy_blake2b(b"", width=32)

    @pytest.mark.parametrize("width", [0, 65, -1])
    def test_width_outside_one_to_sixty_four_raises(self, width: int):
        with pytest.raises(
            ValueError, match=r"^digest_size must be between 1 and 64 bytes$"
        ):
            digest.legacy_blake2b(b"abc", width=width)

    def test_width_is_keyword_only(self):
        with pytest.raises(TypeError):
            digest.legacy_blake2b(b"abc", 32)  # type: ignore[misc]


def test_large_generator_stream_retains_no_absorbed_chunks():
    """16 MiB of fresh 64 KiB chunks must not accumulate inside the stream."""
    block = bytes(range(256)) * 256
    count = 256

    def chunks():
        for _ in range(count):
            yield bytes(bytearray(block))

    gc.collect()
    tracemalloc.start()
    try:
        stream = digest.Stream(chunks())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * len(block)
    # The joined input takes the one-shot parallel path: a different backend route.
    assert stream.raw() == digest.raw(block * count)
