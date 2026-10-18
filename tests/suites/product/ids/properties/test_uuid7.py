"""RFC 9562 bit-layout and ordering laws for ``unstd.ids.uid`` (UUIDv7).

Authority: **RFC 9562 §5.7 / §4** (the UUIDv7 wire layout) as decoded by the
stdlib ``uuid`` module — an independent parser of the same 128-bit structure,
never the generator under test.

Laws (positive):
- version nibble (bits 79..76) is ``7`` and the variant (bits 63..62) is
  ``0b10`` → stdlib reports ``version == 7`` and ``variant == RFC_4122``;
- the embedded 48-bit big-endian ``unix_ts_ms`` field (bits 127..80) is
  **monotone non-decreasing** across successive generations and fits 48 bits;
- the canonical string / bare-hex forms round-trip through ``uuid.UUID``.

Law (adverse): a UUID whose version nibble or variant bits have been tampered
is **detectable** — the stdlib-decoded fields no longer satisfy the v7
predicate. A generator that emitted the wrong version/variant would be caught
by the exact same check.
"""

from __future__ import annotations

import itertools
import uuid

from hypothesis import example, given
from hypothesis import strategies as st

from unstd.ids import uid


def _is_rfc9562_v7(u: uuid.UUID) -> bool:
    """RFC 9562 conformance, judged purely from stdlib-decoded bit fields."""
    return u.version == 7 and u.variant == uuid.RFC_4122


def _timestamp_ms(u: uuid.UUID) -> int:
    """The 48-bit big-endian ``unix_ts_ms`` field (top 48 of 128 bits)."""
    return u.int >> 80


@given(count=st.integers(min_value=1, max_value=64))
@example(count=1)
@example(count=64)
def test_generated_uuid7_layout_and_monotonic_timestamp(count: int) -> None:
    """Every generated v7 has the RFC layout, and time never runs backward."""
    strings = [uid.new() for _ in range(count)]
    parsed = [uuid.UUID(s) for s in strings]

    for text, u in zip(strings, parsed, strict=True):
        assert _is_rfc9562_v7(u)  # version 7 + RFC 9562 variant
        assert str(u) == text  # canonical lowercase-hyphenated round-trip

    stamps = [_timestamp_ms(u) for u in parsed]
    assert all(0 <= ts < (1 << 48) for ts in stamps)  # fits the 48-bit field
    assert all(a <= b for a, b in itertools.pairwise(stamps))  # monotone


@given(count=st.integers(min_value=1, max_value=32))
@example(count=1)
def test_hex_form_round_trips_and_is_v7(count: int) -> None:
    """The bare 32-char hex form parses back identically and stays v7."""
    for _ in range(count):
        hex_form = uid.new_hex()
        u = uuid.UUID(hex=hex_form)
        assert len(hex_form) == 32
        assert u.hex == hex_form  # exact hex round-trip
        assert _is_rfc9562_v7(u)


@given(bad_version=st.integers(min_value=0, max_value=15).filter(lambda v: v != 7))
@example(bad_version=4)  # a v4 masquerading as the time-ordered v7
@example(bad_version=0)
def test_tampered_version_is_detected(bad_version: int) -> None:
    """Overwriting the version nibble is caught by the stdlib-decoded check."""
    u = uuid.UUID(uid.new())
    assert _is_rfc9562_v7(u)

    b = bytearray(u.bytes)
    b[6] = (b[6] & 0x0F) | (bad_version << 4)  # version nibble = high nibble of byte 6
    tampered = uuid.UUID(bytes=bytes(b))

    assert tampered.version == bad_version
    assert not _is_rfc9562_v7(tampered)


@given(variant_bits=st.sampled_from((0b00, 0b01, 0b11)))  # any non-RFC variant
@example(variant_bits=0b00)
def test_tampered_variant_is_detected(variant_bits: int) -> None:
    """Corrupting the variant bits away from 0b10 is caught."""
    u = uuid.UUID(uid.new())
    assert _is_rfc9562_v7(u)

    b = bytearray(u.bytes)
    b[8] = (b[8] & 0x3F) | (variant_bits << 6)  # variant = top two bits of byte 8
    tampered = uuid.UUID(bytes=bytes(b))

    assert tampered.variant != uuid.RFC_4122
    assert not _is_rfc9562_v7(tampered)
