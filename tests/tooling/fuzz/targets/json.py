"""JSON v1: selector byte modulo four, followed by payload (empty selects raw).

Modes 0/1 decode raw bytes/UTF-8 text (surrogatepass, then surrogateescape for
invalid sequences), 2 encodes bounded byte-derived builtin values, and 3 parses
then encodes bounded JSON. Every result, exact builtin type, dict insertion
order, NaN, signed zero, exception class and diagnostic has a stdlib oracle.
The raw grammar domain excludes nesting above 96 and digit runs above 512;
these are campaign bounds rather than changes to jsonx's public contract.
Generated trees have depth <= 3, width <= 8 and at most 1024 text bytes. No
recursion/digit-limit changes, callbacks supplied by input, or random state run.
"""

# Fuzz entry points run without pytest; inspect exact rejection objects directly.
# ruff: noqa: PT017

from __future__ import annotations

import io
import json as stdjson
import math
from typing import TYPE_CHECKING, Any

from unstd.serde import jsonx


if TYPE_CHECKING:
    from collections.abc import Callable


MAX_DEPTH = 96
MAX_DIGITS = 512


def text(raw: bytes) -> str:
    """Keep valid Unicode, including encoded surrogates, and arbitrary bytes."""
    try:
        return raw.decode("utf-8", "surrogatepass")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "surrogateescape")


def bounded(raw: bytes | str) -> bool:
    """Bound JSON grammar depth, respecting encodings, strings and escapes."""
    if isinstance(raw, str):
        source = raw
    else:
        try:
            source = raw.decode(stdjson.detect_encoding(raw), "surrogatepass")
        except UnicodeDecodeError:
            # stdlib rejects invalid text encoding before parsing grammar.
            return True
    depth = digits = 0
    quoted = escaped = False
    for char in source:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                return False
        elif char in "]}":
            depth = max(depth - 1, 0)
        digits = digits + 1 if "0" <= char <= "9" else 0
        if digits > MAX_DIGITS:
            return False
    return True


def same(actual: Any, expected: Any) -> None:
    """Assert JSON types and ordered structure without losing NaN or -0.0."""
    pending = [(actual, expected)]
    while pending:
        got, want = pending.pop()
        assert type(got) is type(want), (type(got), type(want))
        if isinstance(want, float):
            assert math.isnan(got) if math.isnan(want) else got.hex() == want.hex()
        elif isinstance(want, dict):
            assert list(got) == list(want)
            pending.extend((got[key], value) for key, value in want.items())
        elif isinstance(want, list):
            assert len(got) == len(want)
            pending.extend(zip(got, want, strict=True))
        else:
            assert got == want


def parity(
    actual: Callable[[], Any],
    oracle: Callable[[], Any],
    errors: tuple[type[Exception], ...] = (ValueError,),
) -> tuple[bool, Any]:
    """Compare documented rejections too; unexpected exceptions escape."""
    try:
        expected = oracle()
    except errors as expected_error:
        try:
            actual()
        except errors as actual_error:
            assert type(actual_error) is type(expected_error)
            assert actual_error.args == expected_error.args
            if isinstance(expected_error, stdjson.JSONDecodeError):
                assert (
                    actual_error.doc,
                    actual_error.pos,
                    actual_error.lineno,
                    actual_error.colno,
                ) == (
                    expected_error.doc,
                    expected_error.pos,
                    expected_error.lineno,
                    expected_error.colno,
                )
        else:
            msg = "unstd accepted input rejected by independent oracle"
            raise AssertionError(msg)
        return False, None
    same(actual(), expected)
    return True, expected


def value(raw: bytes, control: int = 0) -> Any:
    """Build finite-size native values that reach all encoder scalar paths."""
    sample = raw[:1024]
    if control & 64:
        # Exercise eligible native snapshots as well as the rich fallback tree.
        # A byte-derived signed 64-bit integer never defeats the native proof.
        return {
            "rows": [
                {"label": text(sample[offset : offset + 64]), "index": offset}
                for offset in range(0, min(len(sample), 256), 64)
            ],
            "flags": [True, None, False],
            "number": int.from_bytes(sample[:8], "big", signed=True),
        }
    number = int.from_bytes(sample[:16], "big", signed=True)
    strings = [text(sample[offset : offset + 64]) for offset in range(0, 256, 64)]
    scalars = [None, False, True, number, -0.0, float("nan"), float("inf"), *strings]
    return {
        "payload": text(sample),
        "values": scalars[:8],
        "nested": [{str(index): item} for index, item in enumerate(strings)],
        "finite": number / 256,
    }


def _encode(obj: Any, control: int) -> None:
    options = {
        "ensure_ascii": bool(control & 1),
        "sort_keys": bool(control & 2),
        "allow_nan": bool(control & 4),
        "indent": [None, 0, 2, "\t"][(control >> 3) % 4],
        "separators": (",", ":") if control & 32 else None,
    }
    parity(lambda: jsonx.dumps(obj, **options), lambda: stdjson.dumps(obj, **options))
    parity(
        lambda: jsonx.dumpb(obj, sort_keys=bool(control & 2)),
        lambda: stdjson.dumps(
            obj, ensure_ascii=False, separators=(",", ":"), sort_keys=bool(control & 2)
        ).encode("utf-8"),
    )
    parity(
        lambda: jsonx.canonical(obj),
        lambda: stdjson.dumps(obj, sort_keys=True, separators=(",", ":")).encode(),
    )
    got, want = io.StringIO(), io.StringIO()
    parity(
        lambda: jsonx.dump(obj, got, **options),
        lambda: stdjson.dump(obj, want, **options),
    )
    assert got.getvalue() == want.getvalue()


def fuzz(data: bytes) -> None:
    """Exercise raw decoding and independently specified encoding contracts."""
    selector, raw = (data[0], data[1:]) if data else (0, b"")
    mode = selector % 4
    if mode == 2:
        _encode(value(raw, selector), selector)
        return
    source = text(raw) if mode == 1 else raw
    if not bounded(source):
        return
    accepted, decoded = parity(
        lambda: jsonx.loads(source), lambda: stdjson.loads(source)
    )
    parity(
        lambda: jsonx.load(
            io.StringIO(source) if isinstance(source, str) else io.BytesIO(source)
        ),
        lambda: stdjson.load(
            io.StringIO(source) if isinstance(source, str) else io.BytesIO(source)
        ),
    )
    # The pairs hook exposes duplicate-key and object-order behavior independently.
    parity(
        lambda: jsonx.loads(source, object_pairs_hook=list),
        lambda: stdjson.loads(source, object_pairs_hook=list),
    )
    if mode == 3 and accepted:
        _encode(decoded, selector)
