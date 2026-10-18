"""Typed JSONL contracts: schema validation, streaming, diagnostics, and typing.

Backend-dependent cases skip individually so the subprocess proof that untyped
JSONL remains usable without any serde backend also runs on a base install.
"""

from __future__ import annotations

import gzip
import io
import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from unstd.serde import ndjson


if TYPE_CHECKING:
    from pathlib import Path


class _HookError(RuntimeError):
    """A user hook failure that must survive the record error policy."""


class _Token:
    """A foreign value used to exercise msgspec's user decoding hook."""

    def __init__(self, value: object) -> None:
        self.value = value


@pytest.fixture
def typed_backend():
    msgspec = pytest.importorskip("msgspec")
    from unstd.serde import structs

    class Row(msgspec.Struct, forbid_unknown_fields=True):
        idx: int
        label: str
        scores: list[int]

    return SimpleNamespace(
        msgspec=msgspec, structs=structs, Row=Row, codec=structs.Codec(Row)
    )


def _read(reader: str, text: str | bytes, **kwargs: object) -> object:
    """Exercise each public reader with the same physical records."""
    if reader == "iter_loads":
        lines = text.split(b"\n") if isinstance(text, bytes) else text.split("\n")
        return list(ndjson.iter_loads(lines, **kwargs))
    if reader == "load":
        handle = io.BytesIO(text) if isinstance(text, bytes) else io.StringIO(text)
        return ndjson.load(handle, **kwargs)
    return ndjson.loads(text, **kwargs)


@pytest.mark.parametrize("reader", ["loads", "load", "iter_loads"])
@pytest.mark.parametrize("binary", [False, True])
def test_readers_decode_directly_into_the_codec_schema(
    typed_backend, reader: str, binary: bool
) -> None:
    row = typed_backend.Row(1, "café", [2, 3])
    text = '\n \t\n{"idx":1,"label":"café","scores":[2,3]}\r\n\n'
    document = text.encode() if binary else text
    assert _read(reader, document, codec=typed_backend.codec) == [row]


@pytest.mark.parametrize("reader", ["loads", "load", "iter_loads"])
@pytest.mark.parametrize("invalid", ['"1"', "true", "null", "[]", "{}"])
def test_wrong_field_types_are_schema_errors(typed_backend, reader, invalid) -> None:
    text = f'{{"idx":{invalid},"label":"wrong","scores":[]}}\n'
    with pytest.raises(ndjson.NDJSONDecodeError) as caught:
        _read(reader, text, codec=typed_backend.codec)
    assert isinstance(caught.value.err, typed_backend.msgspec.ValidationError)
    assert "$.idx" in str(caught.value.err)


def test_codec_strictness_and_unknown_field_policy_are_preserved(typed_backend) -> None:
    loose = typed_backend.structs.Codec(typed_backend.Row, strict=False)
    raw = '{"idx":"1","label":"coerced","scores":["2"]}\n'
    assert ndjson.loads(raw, codec=loose) == [typed_backend.Row(1, "coerced", [2])]
    with pytest.raises(ndjson.NDJSONDecodeError, match="unknown"):
        ndjson.loads(
            '{"idx":1,"label":"extra","scores":[],"surprise":true}\n',
            codec=typed_backend.codec,
        )


@pytest.mark.parametrize("reader", ["loads", "load", "iter_loads"])
@pytest.mark.parametrize("skip_errors", [False, True])
def test_errors_report_once_with_physical_lines_and_original_cause(
    typed_backend, reader: str, skip_errors: bool
) -> None:
    errors = []
    text = (
        '\n{"idx":1,"label":"good","scores":[]}\n \t\n'
        '{"idx":2,"label":"bad","scores":[1,"wrong"]}\n'
        '{broken}\n{"idx":3,"label":"last","scores":[]}\n'
    )
    kwargs = {
        "codec": typed_backend.codec,
        "skip_errors": skip_errors,
        "on_error": errors.append,
    }
    if skip_errors:
        assert _read(reader, text, **kwargs) == [
            typed_backend.Row(1, "good", []),
            typed_backend.Row(3, "last", []),
        ]
        assert [error.lineno for error in errors] == [4, 5]
        assert isinstance(errors[1].err, typed_backend.msgspec.DecodeError)
        assert not isinstance(errors[1].err, typed_backend.msgspec.ValidationError)
    else:
        with pytest.raises(ndjson.NDJSONDecodeError) as caught:
            _read(reader, text, **kwargs)
        assert errors == [caught.value]
        assert caught.value.__cause__ is caught.value.err
    assert errors[0].lineno == 4
    assert isinstance(errors[0].err, typed_backend.msgspec.ValidationError)
    assert "$.scores[1]" in str(errors[0].err)


@pytest.mark.parametrize("reader", ["loads", "load", "iter_loads"])
@pytest.mark.parametrize("skip_errors", [False, True])
def test_error_callback_exceptions_propagate_unchanged(
    typed_backend, reader: str, skip_errors: bool
) -> None:
    failure = _HookError("callback failed")
    seen = []

    def report(error):
        seen.append(error)
        raise failure

    with pytest.raises(_HookError) as caught:
        _read(
            reader,
            '{"idx":"bad","label":"x","scores":[]}\n',
            codec=typed_backend.codec,
            skip_errors=skip_errors,
            on_error=report,
        )
    assert caught.value is failure
    assert len(seen) == 1


@pytest.mark.parametrize("reader", ["loads", "load", "iter_loads"])
def test_typed_rows_never_route_through_untyped_json(
    typed_backend, reader: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def untyped_parse_was_used(_raw):
        pytest.fail("typed JSONL parsed an intermediate untyped JSON value")

    monkeypatch.setattr(ndjson, "_parse", untyped_parse_was_used)
    assert _read(
        reader,
        '{"idx":1,"label":"direct","scores":[]}\n',
        codec=typed_backend.codec,
    ) == [typed_backend.Row(1, "direct", [])]


@pytest.mark.parametrize("reader", ["loads", "load", "iter_loads"])
def test_decode_is_called_once_per_nonblank_row_even_on_schema_failure(
    typed_backend, reader: str
) -> None:
    decode = typed_backend.codec.decode
    seen = []

    def counted(raw):
        seen.append(raw)
        return decode(raw)

    typed_backend.codec.decode = counted
    first = '{"idx":1,"label":"first","scores":[]}'
    invalid = '{"idx":"bad","label":"second","scores":[]}'
    with pytest.raises(ndjson.NDJSONDecodeError):
        _read(reader, f"{first}\n\n{invalid}\n", codec=typed_backend.codec)
    assert len(seen) == 2


@pytest.mark.parametrize("skip_errors", [False, True])
def test_custom_decoder_hook_exceptions_are_not_row_errors(
    typed_backend, skip_errors: bool
) -> None:
    failure = _HookError("decoder failed")
    calls = []
    errors = []

    def hook(spec, value):
        assert spec is _Token
        calls.append(value)
        raise failure

    codec = typed_backend.structs.Codec(_Token, dec_hook=hook)
    with pytest.raises(_HookError) as caught:
        ndjson.loads(
            '"token"\n', codec=codec, skip_errors=skip_errors, on_error=errors.append
        )
    assert caught.value is failure
    assert calls == ["token"]
    assert errors == []


def test_schema_failure_does_not_replay_successful_decoder_hooks(typed_backend) -> None:
    class HookRow(typed_backend.msgspec.Struct):
        token: _Token
        count: int

    calls = []

    def hook(spec, value):
        assert spec is _Token
        calls.append(value)
        return _Token(value)

    codec = typed_backend.structs.Codec(HookRow, dec_hook=hook)
    with pytest.raises(ndjson.NDJSONDecodeError, match="count"):
        ndjson.loads(
            '{"token":"first","count":1}\n{"token":"second","count":"bad"}\n',
            codec=codec,
        )
    assert calls == ["first", "second"]


def test_typed_stream_is_lazy_stops_at_failure_and_leaves_input_open(
    typed_backend,
) -> None:
    handle = io.StringIO(
        '{"idx":1,"label":"first","scores":[]}\n'
        '{"idx":"bad","label":"second","scores":[]}\n'
        '{"idx":3,"label":"unread","scores":[]}\n'
    )
    rows = ndjson.iter_loads(handle, codec=typed_backend.codec)
    assert handle.tell() == 0
    assert next(rows) == typed_backend.Row(1, "first", [])
    assert handle.readline().startswith('{"idx":"bad"')
    handle.seek(0)
    rows = ndjson.iter_loads(handle, codec=typed_backend.codec)
    next(rows)
    with pytest.raises(ndjson.NDJSONDecodeError):
        next(rows)
    assert not handle.closed
    assert handle.readline().startswith('{"idx":3,')


@pytest.mark.parametrize("padding", ["\u00a0", "\u2028 \t", "\v\f"])
def test_text_exotic_outer_whitespace_is_removed_before_single_decode(
    typed_backend, padding: str
) -> None:
    decode = typed_backend.codec.decode
    seen = []

    def counted(raw):
        seen.append(raw)
        return decode(raw)

    typed_backend.codec.decode = counted
    raw = '{"idx":1,"label":"padded","scores":[]}'
    assert ndjson.loads(f"{padding}{raw}{padding}\n", codec=typed_backend.codec) == [
        typed_backend.Row(1, "padded", [])
    ]
    assert len(seen) == 1


@pytest.mark.parametrize("binary", [False, True])
def test_only_json_newlines_split_typed_records(typed_backend, binary: bool) -> None:
    row = typed_backend.Row(1, "a\u0085b\u2028c\u2029d\r\v\f", [])
    raw = (
        json.dumps(
            {"idx": row.idx, "label": row.label, "scores": row.scores},
            ensure_ascii=False,
        )
        + "\n"
    )
    assert ndjson.loads(raw.encode() if binary else raw, codec=typed_backend.codec) == [
        row
    ]


def test_typed_writers_use_the_codec_and_terminate_each_record(
    typed_backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [typed_backend.Row(1, "café", [2]), typed_backend.Row(2, "a\nb", [])]
    expected = b"".join(typed_backend.codec.encode(row) + b"\n" for row in rows)

    def untyped_encode_was_used(_row):
        pytest.fail("typed JSONL ignored the supplied encoder")

    monkeypatch.setattr(ndjson, "_line", untyped_encode_was_used)
    assert ndjson.dumpb(rows, codec=typed_backend.codec) == expected
    assert ndjson.dumps(rows, codec=typed_backend.codec) == expected.decode()
    for handle in [io.StringIO(), io.BytesIO()]:
        assert ndjson.dump(rows, handle, codec=typed_backend.codec) == 2
        assert not handle.closed
        actual = handle.getvalue()
        assert actual == (expected if isinstance(actual, bytes) else expected.decode())
    assert ndjson.loads(expected + expected, codec=typed_backend.codec) == rows + rows


@pytest.mark.parametrize("binary", [False, True])
def test_typed_dump_writes_each_row_before_consuming_the_next(
    typed_backend, binary
) -> None:
    handle = io.BytesIO() if binary else io.StringIO()
    first = typed_backend.Row(1, "first", [])
    second = typed_backend.Row(2, "second", [])
    expected = typed_backend.codec.encode(first) + b"\n"

    def rows():
        yield first
        assert handle.getvalue() == (expected if binary else expected.decode())
        yield second

    assert ndjson.dump(rows(), handle, codec=typed_backend.codec) == 2


def test_typed_encoder_hook_exceptions_propagate_unchanged(typed_backend) -> None:
    failure = _HookError("encoder failed")

    def hook(_value):
        raise failure

    codec = typed_backend.structs.Codec(_Token, enc_hook=hook)
    for write in [ndjson.dumps, ndjson.dumpb]:
        with pytest.raises(_HookError) as caught:
            write([_Token("value")], codec=codec)
        assert caught.value is failure
    with pytest.raises(_HookError) as caught:
        ndjson.dump([_Token("value")], io.BytesIO(), codec=codec)
    assert caught.value is failure


@pytest.mark.parametrize("text_mode", [False, True])
def test_gzip_typed_roundtrip_preserves_handle_ownership(
    typed_backend, tmp_path: Path, text_mode: bool
) -> None:
    path = tmp_path / "typed.jsonl.gz"
    rows = [typed_backend.Row(1, "compressed", [1, 2])]
    with gzip.open(path, "wt" if text_mode else "wb") as handle:
        assert ndjson.dump(rows, handle, codec=typed_backend.codec) == 1
        assert not handle.closed
    with gzip.open(path, "rt" if text_mode else "rb") as handle:
        assert ndjson.load(handle, codec=typed_backend.codec) == rows
        assert not handle.closed


@pytest.mark.parametrize("reader", ["loads", "load", "iter_loads"])
def test_untyped_error_callback_has_the_same_record_policy(reader: str) -> None:
    errors = []
    assert _read(
        reader, "1\n\n{bad}\n2\n", skip_errors=True, on_error=errors.append
    ) == [1, 2]
    assert len(errors) == 1
    assert errors[0].lineno == 3
    assert isinstance(errors[0].err, json.JSONDecodeError)


def test_untyped_jsonl_and_error_reporting_work_without_serde_backends() -> None:
    script = textwrap.dedent(
        """\
        import importlib.abc
        import io
        import sys

        class BlockSerde(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in {"msgspec", "orjson", "pybase64"}:
                    raise ModuleNotFoundError("serde backend deliberately unavailable")
                return None

        sys.meta_path.insert(0, BlockSerde())
        from unstd.serde import ndjson

        rows = [{"count": 2**100, "label": "café"}, None]
        raw = ndjson.dumpb(rows, codec=None)
        assert ndjson.loads(raw, codec=None) == rows
        assert list(ndjson.iter_loads(raw.split(b"\\n"), codec=None)) == rows
        assert ndjson.load(io.BytesIO(raw), codec=None) == rows
        text = io.StringIO()
        assert ndjson.dump(rows, text, codec=None) == 2
        assert ndjson.dumps(rows, codec=None) == text.getvalue()
        errors = []
        assert ndjson.loads("1\\n{bad}\\n2\\n", skip_errors=True, on_error=errors.append) == [1, 2]
        assert len(errors) == 1 and errors[0].lineno == 2
        assert "msgspec" not in sys.modules
        assert "unstd.serde.structs" not in sys.modules
        try:
            from unstd.serde import structs
        except ImportError as error:
            assert "unstd[serde]" in str(error), str(error)
        else:
            raise AssertionError("typed codec imported without msgspec")
        """
    )
    result = subprocess.run(  # noqa: S603 — fixed script and current interpreter
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_consumer_typing_infers_records_and_rejects_mismatched_writes(
    typed_backend, tmp_path: Path
) -> None:
    mypy = pytest.importorskip("mypy.api")
    valid = tmp_path / "valid_consumer.py"
    valid.write_text(
        textwrap.dedent(
            """\
            from collections.abc import Iterator
            import io
            from typing import assert_type
            import msgspec
            from unstd.serde import ndjson, structs
            from unstd.serde.jsonx import Json

            class Row(msgspec.Struct):
                idx: int

            codec = structs.Codec(Row)
            assert_type(ndjson.iter_loads([], codec=codec), Iterator[Row])
            assert_type(ndjson.loads("", codec=codec), list[Row])
            assert_type(ndjson.load(io.StringIO(), codec=codec), list[Row])
            assert_type(ndjson.load(io.BytesIO(), codec=codec), list[Row])
            assert_type(ndjson.loads(""), list[Json])
            assert_type(ndjson.dumps([Row(1)], codec=codec), str)
            assert_type(ndjson.dumpb([Row(1)], codec=codec), bytes)
            assert_type(ndjson.dump([Row(1)], io.StringIO(), codec=codec), int)

            class MethodCodec:
                def encode(self, value: Row, /) -> bytes:
                    return codec.encode(value)

                def decode(self, raw: str | bytes, /) -> Row:
                    return codec.decode(raw)

            method_codec = MethodCodec()
            assert_type(ndjson.iter_loads([], codec=method_codec), Iterator[Row])
            assert_type(ndjson.loads("", codec=method_codec), list[Row])
            assert_type(ndjson.load(io.StringIO(), codec=method_codec), list[Row])
            assert_type(ndjson.dumps([Row(1)], codec=method_codec), str)
            assert_type(ndjson.dumpb([Row(1)], codec=method_codec), bytes)
            assert_type(ndjson.dump([Row(1)], io.BytesIO(), codec=method_codec), int)
            """
        ),
        encoding="utf-8",
    )
    options = [
        "--strict",
        "--no-incremental",
        "--follow-imports=silent",
        "--cache-dir",
        str(tmp_path / "mypy-cache"),
        "--no-error-summary",
    ]
    stdout, stderr, status = mypy.run([*options, str(valid)])
    assert status == 0, stdout + stderr
    invalid = tmp_path / "invalid_consumer.py"
    invalid.write_text(
        valid.read_text(encoding="utf-8")
        + '\nndjson.dumps(["wrong"], codec=codec)\n'
        + 'ndjson.dumpb(["wrong"], codec=codec)\n'
        + 'ndjson.dump(["wrong"], io.BytesIO(), codec=codec)\n'
        + 'ndjson.dumps(["wrong"], codec=method_codec)\n'
        + 'ndjson.dumpb(["wrong"], codec=method_codec)\n'
        + 'ndjson.dump(["wrong"], io.BytesIO(), codec=method_codec)\n',
        encoding="utf-8",
    )
    stdout, stderr, status = mypy.run([*options, str(invalid)])
    assert status == 1, stdout + stderr
    assert stdout.count("error:") == 6, stdout + stderr
