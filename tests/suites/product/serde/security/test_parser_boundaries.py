"""Hostile JSON/Base64/NDJSON inputs preserve strict, observable boundaries.

Expectations come from stdlib codecs and explicit stream histories. Only
documented rejection is accepted; failures never become silently dropped data
unless the caller selected NDJSON's skip policy.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import io
import json
from typing import TYPE_CHECKING

from hypothesis import given
from hypothesis import strategies as st
import pytest

from tests.support.backends import require_backend
from tests.support.parser_cases import parser_case
from unstd.serde import b64, jsonx, ndjson


if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(params=["stdlib", "installed"])
def b64_backend(request, monkeypatch):
    if request.param == "stdlib":
        monkeypatch.setattr(b64, "_b64", base64)
        monkeypatch.setattr(b64, "_HAVE_PYBASE64", False)
    else:
        backend = require_backend("pybase64")
        monkeypatch.setattr(b64, "_b64", backend)
        monkeypatch.setattr(b64, "_HAVE_PYBASE64", True)


@given(raw=st.binary(max_size=256))
def test_standard_base64_arbitrary_bytes_match_the_strict_stdlib_oracle(raw):
    try:
        expected = base64.b64decode(raw, validate=True)
    except binascii.Error:
        with pytest.raises(binascii.Error):
            b64.decode(raw)
    else:
        assert b64.decode(raw) == expected


@pytest.mark.parametrize("junk", [b"!", b"\n", b"\r", b"\t", b"\x00", b"\xff", b" "])
@pytest.mark.parametrize("position", [0, 2, 4])
def test_standard_base64_never_discards_junk_at_any_position(
    b64_backend, junk, position
):
    clean = base64.b64encode(b"fixture")
    hostile = clean[:position] + junk + clean[position:]
    assert b64.decode(clean) == b"fixture"
    with pytest.raises(binascii.Error):
        b64.decode(hostile)


@pytest.mark.parametrize("source", ["é", "😀", "\ud800", "Zg==é"])
def test_base64_non_ascii_text_is_refused_on_both_alphabet_modes(b64_backend, source):
    for url in (False, True):
        with pytest.raises(ValueError):  # noqa: PT011 — backend/stdlib encoding rejection
            b64.decode(source, url=url)
    assert b64.decode("Zg==") == b"f"


@pytest.mark.parametrize(
    "source", [b"A", b"AAA", b"Zg=", b"Zg===", b"Zg==junk", b"=Zg==", b"Z=g="]
)
def test_base64_invalid_lengths_padding_and_trailing_data_are_rejected(
    b64_backend, source
):
    with pytest.raises(binascii.Error):
        base64.b64decode(source, validate=True)
    with pytest.raises(binascii.Error):
        b64.decode(source)
    assert b64.decode(base64.b64encode(bytes(range(256)))) == bytes(range(256))


@pytest.mark.parametrize(
    "raw",
    [b"{", b"[1,]", b'{"a":1}junk', b'"\x00"', b'"\xff"', b"\xef\xbb", b"\x00\xd8"],
)
def test_json_rejection_matches_the_interpreter_including_encodings(raw):
    try:
        json.loads(raw)
    except (ValueError, UnicodeError) as expected:
        with pytest.raises(type(expected)):
            jsonx.loads(raw)
    else:
        pytest.fail(
            "the hostile corpus entry must be rejected by the independent oracle"
        )
    assert jsonx.loads(b'{"valid":[1,true,null]}') == {"valid": [1, True, None]}


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_ingress_policy_can_refuse_nonfinite_constants_without_retry(constant):
    seen = []
    error = RuntimeError("nonfinite input denied by caller")

    def refuse(value):
        seen.append(value)
        raise error

    with pytest.raises(RuntimeError) as caught:
        jsonx.loads("[" + constant + "]", parse_constant=refuse)
    assert caught.value is error
    assert seen == [constant]
    assert jsonx.loads("[1]", parse_constant=refuse) == [1]
    assert seen == [constant]


def test_json_hook_failure_is_not_retried_or_wrapped_as_invalid_input():
    seen = []
    error = LookupError("caller hook failure")

    def hook(value):
        seen.append(value)
        raise error

    with pytest.raises(LookupError) as caught:
        jsonx.loads('{"outer":{"inner":1}}', object_hook=hook)
    assert caught.value is error
    assert seen == [{"inner": 1}]
    assert jsonx.loads('{"valid":true}') == {"valid": True}


def test_json_duplicates_and_opaque_code_text_follow_json_without_execution(tmp_path):
    marker = tmp_path / "must-not-exist"
    text = f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')"
    wire = json.dumps({"code": text})
    assert jsonx.loads(wire) == {"code": text}
    assert jsonx.loads('{"role":"admin","role":"reader"}') == {"role": "reader"}
    assert not marker.exists()


@pytest.mark.parametrize(
    ("raw", "success_type"),
    [
        (b"[" * 20000 + b"]" * 20000, "list"),
        (b'{"x":' * 5000, None),
        (b"1" * 100000, None),
    ],
    ids=["deep-array", "truncated-object", "oversized-integer"],
)
def test_hostile_json_depth_and_integer_inputs_are_contained_in_a_worker(
    raw, success_type
):
    assert parser_case("json", b'{"control":[1,2]}', inspect="value") == {
        "kind": "value",
        "type": "dict",
        "value": {"control": [1, 2]},
    }
    result = parser_case("json", raw)
    # Valid nesting has no fixed portable ceiling: CPython 3.14 can accept this
    # array within its C stack budget. Successful decoding must still finish in
    # the bounded worker; malformed input and oversized integers must reject.
    if result["kind"] == "value":
        assert success_type is not None
        assert result == {"kind": "value", "type": success_type}
    else:
        assert result["type"] in {"RecursionError", "JSONDecodeError", "ValueError"}


@pytest.mark.parametrize("source", ["{broken}", "\x00", b'"\xff"', "[1,]"])
def test_ndjson_physical_line_and_error_callback_are_exact(source):
    binary = isinstance(source, bytes)
    lines = [
        b'{"first":1}\n',
        b"\n",
        b" \t\n",
        source if binary else source.encode(),
        b'{"last":2}\n',
    ]
    records = lines if binary else [line.decode() for line in lines]
    seen = []
    assert list(ndjson.iter_loads(records, skip_errors=True, on_error=seen.append)) == [
        {"first": 1},
        {"last": 2},
    ]
    assert len(seen) == 1
    assert seen[0].lineno == 4
    assert isinstance(seen[0].err, ValueError)
    with pytest.raises(ndjson.NDJSONDecodeError) as caught:
        list(ndjson.iter_loads(records))
    assert caught.value.lineno == 4
    assert caught.value.__cause__ is caught.value.err


def test_ndjson_error_observer_failure_propagates_once_and_stops_consumption():
    consumed = []
    failure = RuntimeError("observer failed")
    seen = []

    def lines() -> Iterator[str]:
        for line in ['{"ok":1}\n', "bad\n", '{"later":2}\n']:
            consumed.append(line)
            yield line

    def observe(error):
        seen.append(error.lineno)
        raise failure

    stream = ndjson.iter_loads(lines(), skip_errors=True, on_error=observe)
    assert next(stream) == {"ok": 1}
    with pytest.raises(RuntimeError) as caught:
        next(stream)
    assert caught.value is failure
    assert seen == [2]
    assert consumed == ['{"ok":1}\n', "bad\n"]


def test_ndjson_stream_io_error_cannot_be_disguised_as_a_skipped_record():
    failure = OSError("read fault")
    observed = []

    def lines() -> Iterator[str]:
        yield '{"ok":1}\n'
        raise failure

    stream = ndjson.iter_loads(lines(), skip_errors=True, on_error=observed.append)
    assert next(stream) == {"ok": 1}
    with pytest.raises(OSError, match="read fault") as caught:
        next(stream)
    assert caught.value is failure
    assert observed == []


def test_truncated_gzip_is_an_io_failure_even_when_json_errors_are_skipped():
    raw = b'{"ok":1}\n{"last":2}\n'
    good = gzip.compress(raw, mtime=0)
    with gzip.GzipFile(fileobj=io.BytesIO(good)) as source:
        assert ndjson.load(source) == [{"ok": 1}, {"last": 2}]
    with (
        gzip.GzipFile(fileobj=io.BytesIO(good[:-8])) as source,
        pytest.raises(EOFError),
    ):
        ndjson.load(source, skip_errors=True)


@pytest.mark.parametrize("binary", [False, True])
def test_ndjson_writer_failure_preserves_completed_prefix_and_does_not_consume_ahead(
    binary,
):
    failure = OSError("write denied")
    consumed = []

    class FailingText(io.StringIO):
        def write(self, value):
            if self.tell():
                raise failure
            return super().write(value)

    class FailingBinary(io.BytesIO):
        def write(self, value):
            if self.tell():
                raise failure
            return super().write(value)

    def records():
        for value in [1, 2, 3]:
            consumed.append(value)
            yield {"value": value}

    output = FailingBinary() if binary else FailingText()
    with pytest.raises(OSError, match="write denied") as caught:
        ndjson.dump(records(), output)
    assert caught.value is failure
    assert consumed == [1, 2]
    assert output.getvalue() == (b'{"value":1}\n' if binary else '{"value":1}\n')
    assert not output.closed
