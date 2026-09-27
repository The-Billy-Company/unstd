r"""Contract tests for ``unstd.serde.ndjson``.

The module shipped with no test file at all, so every promise in its docstring
was unenforced. Each is pinned here against an **independent oracle** — stdlib
``json``, never ``jsonx`` itself — so a bug that lives in the shared backend
cannot make the expectation agree with the mistake:

1. Every record is newline-terminated, *including the last*, which is what makes
   the output append-safe.
2. Reads split strictly on ``\n``. ``str.splitlines`` also breaks on ``\r``,
   U+0085, U+2028/9, VT and FF — code points that appear **inside** JSON string
   bodies, where splitting on them silently truncates a record. This is the
   module's sharpest divergence from the obvious implementation and the one most
   likely to be "simplified" back into a bug.
3. Blank lines are skipped; a malformed line raises ``NDJSONDecodeError``
   carrying its **1-based** line number, or is dropped under ``skip_errors``.
4. ``iter_loads`` is lazy — it never materializes the input.
5. ``write`` picks bytes vs str from the handle and returns a record count.
"""

from __future__ import annotations

import gzip
import io
import json
from typing import TYPE_CHECKING

import pytest

from unstd.serde import jsonx, ndjson


if TYPE_CHECKING:
    from pathlib import Path


ROWS: list[object] = [
    {"a": 1},
    {"nested": {"b": [1, 2, 3]}},
    [1, "two", None],
    "bare string",
    42,
    3.5,
    True,
    None,
]


def _oracle(rows: list[object]) -> str:
    """The NDJSON these rows must decode back to, built with stdlib json alone."""
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


# ── termination: every line, including the last ─────────────────────────────


def test_dumps_terminates_every_record_including_the_last() -> None:
    out = ndjson.dumps(ROWS)
    assert out.endswith("\n")
    assert out.count("\n") == len(ROWS)


def test_dumpb_is_the_utf8_encoding_of_dumps() -> None:
    assert ndjson.dumpb(ROWS) == ndjson.dumps(ROWS).encode()


def test_trailing_newline_makes_output_append_safe() -> None:
    # Two independently produced blobs concatenated must parse as one stream —
    # this is the whole reason the last record is terminated too.
    first, second = ndjson.dumps([{"a": 1}]), ndjson.dumps([{"b": 2}])
    assert ndjson.loads(first + second) == [{"a": 1}, {"b": 2}]


def test_dumps_agrees_with_a_stdlib_json_oracle() -> None:
    assert ndjson.loads(ndjson.dumps(ROWS)) == json.loads(json.dumps(ROWS))
    assert [json.loads(ln) for ln in _oracle(ROWS).splitlines()] == ROWS


def test_empty_input_produces_empty_output_both_directions() -> None:
    assert ndjson.dumps([]) == ""
    assert ndjson.dumpb([]) == b""
    assert ndjson.loads("") == []
    assert ndjson.loads(b"") == []


# ── the splitlines trap: only \n separates records ──────────────────────────


@pytest.mark.parametrize(
    ("name", "char"),
    [
        ("carriage return", "\r"),
        ("next line U+0085", "\u0085"),
        ("line separator U+2028", "\u2028"),
        ("paragraph separator U+2029", "\u2029"),
        ("vertical tab", "\v"),
        ("form feed", "\f"),
    ],
)
def test_line_breaking_codepoints_inside_a_string_do_not_split_a_record(
    name: str, char: str
) -> None:
    """A record survives every code point ``str.splitlines`` would break on.

    Whether the hazard is *reachable* differs by code point, and the test asks
    rather than assumes: JSON escapes everything below U+0020, so VT/FF/CR come
    back out as two safe ASCII characters. U+0085 and U+2028/9 are above that
    line, so a conforming encoder emits them raw — those are the ones that
    actually reach a reader intact and silently truncate a record under
    ``splitlines``. Both classes must round-trip; only the raw ones can spring
    the trap, and the assertion is conditioned on which happened.
    """
    payload = {"text": f"before{char}after", "name": name}
    line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    assert ndjson.loads(line + "\n") == [payload]
    if char in line:  # the encoder left it raw → splitlines would over-split
        assert len((line + "\n").splitlines()) > 1
    else:  # escaped to a safe two-character sequence instead
        assert json.dumps(char)[1:-1] in line


def test_the_three_raw_separators_are_the_reachable_hazard() -> None:
    """Name the trap outright, so shrinking the parametrize list cannot hide it.

    U+0085, U+2028 and U+2029 survive JSON encoding unescaped. A reader built on
    ``str.splitlines`` sees three lines where there is one record, and each
    fragment is invalid JSON — a corrupted read, not a loud failure.
    """
    payload = {"text": "a\u0085b\u2028c\u2029d"}
    line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    assert len((line + "\n").splitlines()) == 4
    assert ndjson.loads(line + "\n") == [payload]


def test_crlf_writer_is_tolerated() -> None:
    blob = "".join(f"{json.dumps(r, separators=(',', ':'))}\r\n" for r in ROWS)
    assert ndjson.loads(blob) == ROWS


def test_bytes_and_str_blobs_parse_identically() -> None:
    blob = _oracle(ROWS)
    assert ndjson.loads(blob) == ndjson.loads(blob.encode())


# ── blank lines and malformed lines ─────────────────────────────────────────


def test_blank_and_whitespace_lines_are_skipped_interior_and_trailing() -> None:
    blob = '{"a":1}\n\n   \n{"b":2}\n\n'
    assert ndjson.loads(blob) == [{"a": 1}, {"b": 2}]


def test_malformed_line_raises_with_its_one_based_line_number() -> None:
    # Line 3 is the bad one; a 0-based or post-skip count would report 2.
    blob = '{"a":1}\n{"b":2}\n{not json}\n{"d":4}\n'
    with pytest.raises(ndjson.NDJSONDecodeError) as caught:
        ndjson.loads(blob)
    assert caught.value.lineno == 3
    assert "line 3" in str(caught.value)


def test_blank_lines_still_count_toward_the_reported_line_number() -> None:
    """The number must locate the line in the *file*, not among the kept records."""
    blob = '{"a":1}\n\n\n{oops}\n'
    with pytest.raises(ndjson.NDJSONDecodeError) as caught:
        ndjson.loads(blob)
    assert caught.value.lineno == 4


def test_skip_errors_drops_only_the_bad_line() -> None:
    blob = '{"a":1}\n{not json}\n{"c":3}\n'
    assert ndjson.loads(blob, skip_errors=True) == [{"a": 1}, {"c": 3}]


def test_decode_error_is_a_valueerror_and_keeps_the_cause() -> None:
    with pytest.raises(ValueError, match="line 1") as caught:
        ndjson.loads("{nope}\n")
    assert isinstance(caught.value, ndjson.NDJSONDecodeError)
    assert caught.value.err is not None


# ── laziness ────────────────────────────────────────────────────────────────


def test_iter_loads_is_lazy_and_pulls_one_line_at_a_time() -> None:
    consumed: list[int] = []

    def source() -> object:
        for i, row in enumerate(ROWS):
            consumed.append(i)
            yield json.dumps(row, separators=(",", ":"))

    it = ndjson.iter_loads(source())
    assert consumed == []  # nothing read before the first next()
    next(it)
    assert consumed == [0]  # exactly one line pulled, not the whole input
    next(it)
    assert consumed == [0, 1]


def test_iter_loads_does_not_read_past_a_failure() -> None:
    """A strict parse must stop at the bad line, not drain the rest of a stream."""
    reached: list[int] = []

    def source() -> object:
        for i, text in enumerate(['{"a":1}', "{bad}", '{"c":3}']):
            reached.append(i)
            yield text

    with pytest.raises(ndjson.NDJSONDecodeError):
        list(ndjson.iter_loads(source()))
    assert reached == [0, 1]


# ── file objects ────────────────────────────────────────────────────────────


def test_dump_to_a_text_handle_returns_the_record_count() -> None:
    buf = io.StringIO()
    assert ndjson.dump(ROWS, buf) == len(ROWS)
    assert buf.getvalue() == _oracle(ROWS)


def test_dump_to_a_binary_handle_returns_the_record_count() -> None:
    buf = io.BytesIO()
    assert ndjson.dump(ROWS, buf) == len(ROWS)
    assert buf.getvalue() == _oracle(ROWS).encode()


def test_dump_streams_rather_than_buffering_the_corpus() -> None:
    """Each record must reach the handle as it is produced, not at the end."""
    seen: list[int] = []

    class Counting(io.StringIO):
        def write(self, s: str) -> int:
            seen.append(len(seen))
            return super().write(s)

    def source() -> object:
        yield from ROWS

    ndjson.dump(source(), Counting())
    assert len(seen) == len(ROWS)


def test_load_round_trips_through_both_handle_flavours() -> None:
    assert ndjson.load(io.StringIO(_oracle(ROWS))) == ROWS
    assert ndjson.load(io.BytesIO(_oracle(ROWS).encode())) == ROWS


def test_load_honours_skip_errors() -> None:
    handle = io.StringIO('{"a":1}\n{bad}\n{"c":3}\n')
    assert ndjson.load(handle, skip_errors=True) == [{"a": 1}, {"c": 3}]


def test_gzip_handles_round_trip_in_both_modes(tmp_path: Path) -> None:
    """``write`` names gzip as a target, so both of its modes are contract."""
    path = tmp_path / "rows.jsonl.gz"
    with gzip.open(path, "wb") as out:
        assert ndjson.dump(ROWS, out) == len(ROWS)
    with gzip.open(path, "rb") as handle:
        assert ndjson.load(handle) == ROWS
    with gzip.open(path, "wt") as out:  # TextIOWrapper — the text branch
        assert ndjson.dump(ROWS, out) == len(ROWS)
    with gzip.open(path, "rt") as handle:
        assert ndjson.load(handle) == ROWS


def test_dump_requires_a_real_textiobase_for_str_output() -> None:
    """The text branch is selected by type, not by duck-typing.

    A str-only sink that is not an ``io.TextIOBase`` takes the bytes branch and
    fails loudly rather than writing something wrong — pinned here so the sharp
    edge in the signature (``io.TextIOBase | IO[bytes]``) stays a known one.
    """

    class StrOnlySink:
        def write(self, s: str) -> int:
            if not isinstance(s, str):
                raise TypeError(s)
            return len(s)

    with pytest.raises(TypeError):
        ndjson.dump(ROWS, StrOnlySink())  # type: ignore[arg-type]


# ── the one-comprehension fast path answers exactly what the line walk does ────

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


_RECORD = st.one_of(
    st.integers(-(2**63), 2**63 - 1),
    st.text(max_size=5),
    st.dictionaries(st.text(max_size=3), st.integers(-(2**63), 2**63 - 1), max_size=2),
)
_PAD = st.sampled_from(["", " ", "\t", "\r", "\u00a0", "\u2028 "])


@given(rows=st.lists(st.tuples(_PAD, _RECORD, _PAD), max_size=12), blanks=st.booleans())
def test_loads_fast_path_matches_the_stripping_line_walk(
    rows: list[tuple[str, object, str]], blanks: bool
) -> None:
    lines = [f"{a}{jsonx.dumps(r)}{b}" for a, r, b in rows]
    if blanks:
        lines.insert(len(lines) // 2, "  ")
    text = "\n".join(lines)
    expected = [r for _, r, _ in rows]
    assert ndjson.loads(text) == expected == list(ndjson.iter_loads(text.split("\n")))
    if text.isascii():  # `bytes.strip` only ever stripped ASCII whitespace
        assert ndjson.loads(text.encode()) == expected


def test_loads_fast_path_failure_still_names_the_bad_line() -> None:
    with pytest.raises(ndjson.NDJSONDecodeError) as ei:
        ndjson.loads('{"a": 1}\n\n{"b": \n{"c": 3}\n')
    assert ei.value.lineno == 3
    assert ndjson.loads('{"a": 1}\n{"b": \n{"c": 3}\n', skip_errors=True) == [
        {"a": 1},
        {"c": 3},
    ]
