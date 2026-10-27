"""The native array framer identifies bounded immutable spans without parsing.

Expected positions come from independently assembled JSON values and their
known delimiters. Every prefix tests incomplete-window behavior across all
Unicode storage widths. The public reader remains responsible for decoding
and rejecting malformed syntax.
"""

from __future__ import annotations

from contextlib import contextmanager
import io
import json
import sys

import pytest

from unstd.serde import _stream, jsonx


native = pytest.importorskip("unstd.serde._json_native")


@contextmanager
def _observed_default_decodes():
    if not jsonx._HAVE_MSGSPEC:
        pytest.skip("native batch decoding requires the serde extra")
    monitoring = sys.monitoring
    available = [tool for tool in range(6) if monitoring.get_tool(tool) is None]
    if not available:
        pytest.skip("all monitoring tool IDs are already occupied")
    tool = available[-1]
    monitoring.use_tool_id(tool, "unstd stream batch calls")
    calls = []
    loads = jsonx.loads
    batch_decode = native.array_decode
    callers = (_stream.items.__code__, jsonx._array_items.__code__)

    def called(_code, _offset, function, first_argument):
        if function is loads or function is batch_decode:
            calls.append(first_argument)

    try:
        monitoring.register_callback(tool, monitoring.events.CALL, called)
        # Observe this caller only. Monitoring the decoder itself deliberately
        # disables its native dispatch, which would invalidate the route probe.
        for code in callers:
            monitoring.set_local_events(tool, code, monitoring.events.CALL)
        loads("null")  # Refresh decoder instrumentation after setting the tool.
        assert native.dispatch_ready(loads)
        yield calls
    finally:
        for code in callers:
            monitoring.set_local_events(tool, code, 0)
        monitoring.free_tool_id(tool)


def test_complete_default_window_uses_one_authoritative_bulk_decode():
    document = '[{"id":0},{"id":1},{"id":2}]'
    with _observed_default_decodes() as calls:
        assert list(jsonx.iterload(io.StringIO(document))) == [
            {"id": 0},
            {"id": 1},
            {"id": 2},
        ]
    assert calls == [document]


def test_native_refusal_preserves_the_original_per_item_decoder(monkeypatch):
    attempts = []

    def refuse(text, start):
        attempts.append((text, start))
        return NotImplemented

    monkeypatch.setattr(native, "array_boundary", refuse)
    with _observed_default_decodes() as calls:
        assert list(jsonx.iterload(io.StringIO("[1,2,3]"))) == [1, 2, 3]
    assert calls == ["1", "2", "3"]
    assert attempts


@pytest.mark.parametrize(
    "document",
    [
        '["quoted },] inside",2]',
        '["quoted } , inside",2]',
        '[[{"inner":1}],2]',
        '[{"nested":[{"inner":1}]},[{},{}]]',
        '["escaped \\"} , quoted",{"value":2}]',
        '["first },]",["second } ,",{"a":"third } ,"}]]',
    ],
)
@pytest.mark.parametrize("chunk_size", [7, 17, 65536])
def test_reverse_object_candidates_preserve_quotes_and_nested_boundaries(
    document, chunk_size
):
    assert list(
        jsonx.iterload(io.StringIO(document), chunk_size=chunk_size)
    ) == json.loads(document)


@pytest.mark.parametrize(
    "suffix",
    [
        '"unfinished } , content',
        '[{"inner":1}]',
        '{"nested":{"inner":1}',
        '"escaped \\"} , unfinished',
    ],
)
def test_failed_reverse_candidate_preserves_complete_prefix_before_truncation(suffix):
    rows = jsonx.iterload(io.StringIO('[{"ok":1},' + suffix), chunk_size=65536)
    assert next(rows) == {"ok": 1}
    with pytest.raises(json.JSONDecodeError):
        next(rows)


def test_reverse_candidates_do_not_speculate_or_replay_custom_decoder_hooks(
    monkeypatch,
):
    seen, decoded = [], []

    class Decoder(json.JSONDecoder):
        def decode(self, text, **kwargs):
            decoded.append(text)
            return super().decode(text, **kwargs)

    def hook(value):
        seen.append(value)
        return value

    def forbidden_scan(_text, _start):
        pytest.fail("custom callbacks entered native speculative batching")

    monkeypatch.setattr(native, "array_boundary", forbidden_scan)
    document = '[{"a":"quoted } ,"},{"b":[{"c":"nested },]"}]},broken]'
    rows = jsonx.iterload(
        io.StringIO(document),
        chunk_size=65536,
        cls=Decoder,
        object_hook=hook,
    )
    assert next(rows) == {"a": "quoted } ,"}
    assert seen == [{"a": "quoted } ,"}]
    assert next(rows) == {"b": [{"c": "nested },]"}]}
    with pytest.raises(json.JSONDecodeError):
        next(rows)
    assert seen == [
        {"a": "quoted } ,"},
        {"c": "nested },]"},
        {"b": [{"c": "nested },]"}]},
    ]
    assert decoded == [
        '{"a":"quoted } ,"}',
        '{"b":[{"c":"nested },]"}]}',
        "broken",
    ]


def test_one_failed_batch_does_not_reparse_every_remaining_suffix():
    document = "[" + "0," * 1000 + "broken]"
    with _observed_default_decodes() as calls:
        rows = jsonx.iterload(io.StringIO(document))
        for _ in range(1000):
            assert next(rows) == 0
        with pytest.raises(json.JSONDecodeError):
            next(rows)
    assert calls == [document, *(["0"] * 1000), "broken"]


def test_one_byte_reads_do_not_repeatedly_batch_scan_a_growing_large_item(monkeypatch):
    if not jsonx._HAVE_MSGSPEC:
        pytest.skip("native batch decoding requires the serde extra")
    jsonx.loads("null")
    assert native.dispatch_ready(jsonx.loads)
    original = native.array_boundary
    scanned_lengths = []

    def scan(text, start):
        scanned_lengths.append(len(text) - start)
        return original(text, start)

    class OneByteReader(io.StringIO):
        def read(self, size):
            assert size == 65536
            return super().read(1)

    monkeypatch.setattr(native, "array_boundary", scan)
    value = "a" * 100_000
    document = '["' + value + '",2]'
    assert list(jsonx.iterload(OneByteReader(document))) == [value, 2]
    # The initial candidate and one bounded carry can be scanned; a growing
    # unfinished record must then stay on the incremental walker until done.
    assert 1 <= len(scanned_lengths) <= 8
    assert sum(scanned_lengths) <= 3 * len(document)


def test_whitespace_carry_does_not_join_adjacent_number_tokens(monkeypatch):
    if not jsonx._HAVE_MSGSPEC:
        pytest.skip("native batch decoding requires the serde extra")
    original = native.array_boundary
    attempts = []

    def scan(text, start):
        attempts.append(len(text) - start)
        return original(text, start)

    monkeypatch.setattr(native, "array_boundary", scan)
    document = "[1" + " " * 100_000 + "2]"
    with pytest.raises(json.JSONDecodeError):
        list(jsonx.iterload(io.StringIO(document), chunk_size=127))
    assert attempts


def test_replaced_backend_decoder_cannot_enter_native_batching(monkeypatch):
    if not jsonx._HAVE_MSGSPEC:
        pytest.skip("backend guard requires the serde extra")
    calls = []
    original = jsonx._decode

    def decode(text):
        calls.append(text)
        return original(text)

    def forbidden_scan(_text, _start):
        pytest.fail("changed decoder configuration entered native batching")

    monkeypatch.setattr(jsonx, "_decode", decode)
    monkeypatch.setattr(native, "array_boundary", forbidden_scan)
    assert not native.dispatch_ready(jsonx.loads)
    assert list(jsonx.iterload(io.StringIO('[{"id":0},{"id":1},2]'))) == [
        {"id": 0},
        {"id": 1},
        2,
    ]
    assert calls == ['{"id":0}', '{"id":1}', "2"]


@pytest.fixture
def default_decoder():
    if not jsonx._HAVE_MSGSPEC:
        pytest.skip("native batch decoding requires the serde extra")
    jsonx.loads("null")
    assert native.dispatch_ready(jsonx.loads)
    return jsonx.loads


@pytest.mark.parametrize("marker", ["ascii", "café", "界", "😀"])
def test_native_batch_decode_copies_only_the_requested_unicode_span(
    marker, default_decoder
):
    values = [{"marker": marker, "nested": [None, True, 2**160, 1.25]}, marker]
    document = json.dumps(values, ensure_ascii=False)
    interior = document[1:-1]
    prefix = "ignored prefix:"
    source = prefix + interior + ":ignored suffix"
    result = native.array_decode(
        source, len(prefix), len(prefix) + len(interior), default_decoder
    )
    assert result == json.loads(document)
    assert type(result) is list
    assert list(result[0]) == ["marker", "nested"]


def test_native_batch_decode_accepts_an_empty_span(default_decoder):
    assert native.array_decode("unused", 3, 3, default_decoder) == []


@pytest.mark.parametrize("text", [None, bytearray(b"1,2"), [], 1])
def test_native_batch_decode_refuses_mutable_and_non_text_inputs(text):
    assert native.array_decode(text, 0, 0, jsonx.loads) is NotImplemented


def test_native_batch_decode_refuses_subclasses_without_conversion_hooks():
    class Text(str):
        def __str__(self):
            raise AssertionError

        def __len__(self):
            raise AssertionError

        def __getitem__(self, index):
            raise AssertionError

    assert native.array_decode(Text("1,2"), 0, 3, jsonx.loads) is NotImplemented

    class Binary(bytes):
        def __bytes__(self):
            raise AssertionError

        def __len__(self):
            raise AssertionError

        def __getitem__(self, index):
            raise AssertionError

    assert native.array_decode(Binary(b"1,2"), 0, 3, jsonx.loads) is NotImplemented


@pytest.mark.parametrize("marker", ["ascii", "café", "界", "😀"])
def test_native_binary_decode_uses_utf8_byte_offsets(marker, default_decoder):
    values = [{"text": marker, "nested": [True, None, 2**160, 1.25]}, marker]
    document = json.dumps(values, ensure_ascii=False).encode()
    prefix = "ignored café😀:".encode()
    interior = document[1:-1]
    source = prefix + interior + b":ignored suffix"
    assert native.array_decode(
        source, len(prefix), len(prefix) + len(interior), default_decoder
    ) == json.loads(document)


@pytest.mark.parametrize("marker", ["ascii", "café", "界", "😀"])
def test_native_binary_boundary_returns_utf8_byte_indices(marker):
    values = [{"text": marker, "nested": [1, {"quoted": '\\"[,]{}'}]}, marker]
    prefix = "ignored café😀:".encode()
    source = prefix + json.dumps(values, ensure_ascii=False).encode()[1:]
    start = len(prefix)
    closing = len(source) - 1
    assert native.array_boundary(source, start) == (closing, True)
    for stop in range(start, len(source) + 1):
        result = native.array_boundary(source[:stop], start)
        assert result[1] is (closing < stop)
        assert result[0] < stop


@pytest.mark.parametrize("document", [b'"\xff"', b'"\xc2"', b'"\xf0\x9f\x98"'])
def test_native_binary_decode_errors_do_not_retry_stdlib(
    document, default_decoder, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("native binary batch helper retried through stdlib")

    monkeypatch.setattr(jsonx._stdlib, "loads", forbidden)
    with pytest.raises(ValueError, match=r"."):
        native.array_decode(document, 0, len(document), default_decoder)


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("offset", [True, False, 0.0, "0", None, object()])
def test_native_batch_decode_requires_exact_integer_offsets(position, offset):
    bounds = [0, 3]
    bounds[position] = offset
    with pytest.raises(TypeError):
        native.array_decode("1,2", *bounds, jsonx.loads)


def test_native_batch_decode_does_not_coerce_integer_subclasses():
    class Offset(int):
        def __index__(self):
            raise AssertionError

    with pytest.raises(TypeError):
        native.array_decode("1,2", Offset(0), 3, jsonx.loads)
    with pytest.raises(TypeError):
        native.array_decode("1,2", 0, Offset(3), jsonx.loads)


@pytest.mark.parametrize(("start", "end"), [(-1, 3), (0, -1), (2, 1), (0, 4), (4, 4)])
def test_native_batch_decode_rejects_reversed_or_outside_spans(start, end):
    with pytest.raises(ValueError, match=r"outside|reversed"):
        native.array_decode("1,2", start, end, jsonx.loads)


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("offset", [-(2**200), 2**200])
def test_native_batch_decode_rejects_offset_overflow(position, offset):
    bounds = [0, 3]
    bounds[position] = offset
    with pytest.raises(OverflowError):
        native.array_decode("1,2", *bounds, jsonx.loads)


@pytest.mark.parametrize(
    "args", [(), ("1",), ("1", 0), ("1", 0, 1), ("1", 0, 1, None, 0)]
)
def test_native_batch_decode_requires_exactly_four_arguments(args):
    with pytest.raises(TypeError):
        native.array_decode(*args)


def test_native_batch_decode_rejects_keyword_arguments():
    with pytest.raises(TypeError):
        native.array_decode("1", 0, 1, decoder=jsonx.loads)


@pytest.mark.parametrize("document", ["broken", "1,", '{"value":}', "1e+", "01"])
def test_native_batch_parse_errors_are_not_retried_through_stdlib(
    document, default_decoder, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("native batch helper retried invalid input through stdlib")

    monkeypatch.setattr(jsonx._stdlib, "loads", forbidden)
    with pytest.raises(ValueError, match=r"."):
        native.array_decode(document, 0, len(document), default_decoder)


def test_native_batch_decode_refuses_a_replaced_decoder_without_calling_it():
    calls = []

    def replacement(text):
        calls.append(text)
        return []

    assert native.array_decode("1,2", 0, 3, replacement) is NotImplemented
    assert calls == []


def test_native_batch_decode_does_not_accept_the_bound_encoder(default_decoder):
    assert native.array_decode("1,2", 0, 3, jsonx.dumpb) is NotImplemented


def test_native_batch_decode_refuses_replaced_public_globals(
    monkeypatch, default_decoder
):
    calls = []

    def replacement(text):
        calls.append(text)
        return []

    monkeypatch.setattr(jsonx, "loads", replacement)
    assert native.array_decode("1,2", 0, 3, default_decoder) is NotImplemented
    assert calls == []


def test_native_batch_decode_refuses_changed_backend_configuration(
    monkeypatch, default_decoder
):
    calls = []

    def replacement(text):
        calls.append(text)
        return []

    monkeypatch.setattr(jsonx, "_decode", replacement)
    assert native.array_decode("1,2", 0, 3, default_decoder) is NotImplemented
    assert calls == []


@pytest.mark.parametrize("observer", ["profile", "trace"])
def test_native_batch_decode_refuses_active_python_observers(observer, default_decoder):
    def observe(_frame, _event, _argument):
        return observe

    get = sys.getprofile if observer == "profile" else sys.gettrace
    set_observer = sys.setprofile if observer == "profile" else sys.settrace
    previous = get()
    try:
        set_observer(observe)
        assert native.array_decode("1,2", 0, 3, default_decoder) is NotImplemented
    finally:
        set_observer(previous)


@pytest.mark.parametrize("marker", ["ascii", "café", "界", "😀", "\ud800"])
def test_every_window_prefix_returns_only_known_complete_boundaries(marker):
    values = [
        {"value": marker, "quoted": '\\"[,]{}', "nested": [1, {"empty": []}]},
        [marker, True, None, -0.0],
        12345678901234567890,
    ]
    items = [json.dumps(value, ensure_ascii=False) for value in values]
    prefix = "ignored prefix:"
    start = len(prefix)
    source = prefix
    boundaries = []
    for index, item in enumerate(items):
        source += item
        boundaries.append(len(source))
        source += "]" if index == len(items) - 1 else ","
    source += " trailing,ignored]"
    closing = boundaries[-1]
    for stop in range(start, len(source) + 1):
        known = [position for position in boundaries if position < stop]
        expected = (known[-1] if known else -1, closing < stop)
        assert native.array_boundary(source[:stop], start) == expected


@pytest.mark.parametrize("slashes", range(17))
def test_ascii_quote_escape_parity_at_every_truncated_window(slashes):
    value = "\\" * slashes + '"[,]{}' + "\\" * (slashes + 1)
    encoded = json.dumps(value)
    prefix = "skip this:"
    source = prefix + encoded + ",1] ignored"
    start = len(prefix)
    comma = start + len(encoded)
    closing = comma + 2
    for stop in range(start, len(source) + 1):
        if closing < stop:
            expected = (closing, True)
        elif comma < stop:
            expected = (comma, False)
        else:
            expected = (-1, False)
        assert native.array_boundary(source[:stop], start) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", (-1, False)),
        (" ", (-1, False)),
        ("]", (0, True)),
        ("1,2,3", (3, False)),
        ("1,2,3]ignored,]", (5, True)),
        ('1,"unfinished,]', (1, False)),
        ('"escaped\\",]still quoted', (-1, False)),
        ('"ends in escape\\', (-1, False)),
        ("1,} ,3]", (1, False)),
        ("} ,3]", (-1, False)),
        ('[1,2],{"a":[3,4]}', (5, False)),
        ('[1,2],{"a":[3,4]}]', (17, True)),
    ],
)
def test_incomplete_or_malformed_suffixes_leave_decoding_to_the_reader(text, expected):
    assert native.array_boundary(text, 0) == expected


def test_framer_does_not_claim_to_validate_json_syntax():
    # Finding a boundary is only permission to ask the authoritative decoder.
    # This helper deliberately leaves JSON grammar and numeric rules alone.
    assert native.array_boundary("not-json,01]", 0) == (11, True)
    with pytest.raises(json.JSONDecodeError):
        json.loads("[not-json,01]")


@pytest.mark.parametrize("text", [None, bytearray(b"1,2]"), [], 1])
def test_only_exact_immutable_text_is_admitted(text):
    assert native.array_boundary(text, 0) is NotImplemented


def test_unicode_subclass_is_refused_without_running_user_code():
    calls = []

    class Text(str):
        def __str__(self):
            calls.append("str")
            raise AssertionError

        def __len__(self):
            calls.append("len")
            raise AssertionError

        def __getitem__(self, index):
            calls.append("getitem")
            raise AssertionError

    assert native.array_boundary(Text("1,2]"), 0) is NotImplemented
    assert calls == []

    class Binary(bytes):
        def __bytes__(self):
            calls.append("bytes")
            raise AssertionError

        def __len__(self):
            calls.append("len")
            raise AssertionError

        def __getitem__(self, index):
            calls.append("getitem")
            raise AssertionError

    assert native.array_boundary(Binary(b"1,2]"), 0) is NotImplemented
    assert calls == []


@pytest.mark.parametrize("start", [True, False, 0.0, "0", None, object()])
def test_start_requires_an_exact_integer(start):
    with pytest.raises(TypeError):
        native.array_boundary("[]", start)


def test_integer_subclass_is_refused_without_index_conversion():
    class Index(int):
        def __index__(self):
            raise AssertionError

    with pytest.raises(TypeError):
        native.array_boundary("[]", Index(0))


@pytest.mark.parametrize("start", [-1, 3])
def test_start_outside_text_is_rejected(start):
    with pytest.raises(ValueError, match="outside"):
        native.array_boundary("[]", start)


@pytest.mark.parametrize("start", [-(2**200), 2**200])
def test_start_overflow_is_rejected(start):
    with pytest.raises(OverflowError):
        native.array_boundary("[]", start)


def test_end_of_text_is_a_valid_empty_window():
    assert native.array_boundary("[]", 2) == (-1, False)


@pytest.mark.parametrize("args", [(), ("[]",), ("[]", 0, 1)])
def test_framer_requires_exactly_two_arguments(args):
    with pytest.raises(TypeError):
        native.array_boundary(*args)


def test_framer_rejects_keyword_arguments():
    with pytest.raises(TypeError):
        native.array_boundary("[]", start=0)
