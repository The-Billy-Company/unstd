"""Native UTF-8 output retains stdlib ASCII escapes without encoding twice."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.serde import jsonx
from unstd.serde._internal import json_encode


native = pytest.importorskip("unstd.serde._json_native")
pytestmark = pytest.mark.skipif(
    not hasattr(native, "ascii_text"), reason="native text conversion unavailable"
)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "plain ASCII",
        'quote: " slash: \\ comma, colon: newline\n',
        "\x00\b\f\n\r\t\x1f\x7f",
        "\x80\xff\u0100\u2028\u2029\uffff",
        "\U00010000\U0001f600\U0010ffff",
        {"é😀\x7f": ["café", "\u2028", "\U0010ffff"]},
    ],
)
def test_native_ascii_conversion_matches_stdlib(value: object) -> None:
    raw = json.dumps(value, ensure_ascii=False).encode()
    assert native.ascii_text(raw) == json.dumps(value)


@given(st.text(st.characters(blacklist_categories=("Cs",))))
def test_every_unicode_scalar_uses_stdlib_json_escape_spelling(value: str) -> None:
    assert native.ascii_text(json.dumps(value, ensure_ascii=False).encode()) == (
        json.dumps(value)
    )


@pytest.mark.parametrize("options", [{}, {"separators": (",", ":")}, {"indent": 2}])
@pytest.mark.parametrize("sort_keys", [False, True])
def test_unicode_tree_reaches_native_output_without_stdlib_retry(
    options: dict[str, object], sort_keys: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = {"é😀,:": ["café\x7f", {"\u2028": "😀"}]}
    expected = json.dumps(value, sort_keys=sort_keys, **options)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("eligible Unicode output was encoded again through stdlib")

    monkeypatch.setattr(jsonx, "_DEFAULT_ENCODER", SimpleNamespace(encode=forbidden))
    monkeypatch.setattr(json_encode, "_encoder", forbidden)
    assert jsonx.dumps(value, sort_keys=sort_keys, **options) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("\ud800", id="high"),
        pytest.param("\udfff", id="low"),
        pytest.param("\ud800\udfff", id="pair"),
    ],
)
def test_surrogate_trees_keep_stdlib_output(value: str) -> None:
    assert jsonx.dumps({"value": [value]}) == json.dumps({"value": [value]})


@pytest.mark.parametrize(
    "raw", [b"\x80", b"\xff", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"]
)
def test_ascii_conversion_rejects_invalid_utf8(raw: bytes) -> None:
    with pytest.raises(UnicodeDecodeError):
        native.ascii_text(raw)


def test_ascii_conversion_requires_exact_bytes() -> None:
    with pytest.raises(TypeError, match="requires bytes"):
        native.ascii_text(bytearray(b"{}"))
