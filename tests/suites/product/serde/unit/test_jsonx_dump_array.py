"""JSON-array writing consumes one-shot rows with bounded, exact output.

Stdlib compact JSON supplies the independent byte oracle. Sink histories pin
write budgets, short-write recovery, error propagation and caller ownership;
allocation measurements distinguish streaming from materializing the corpus.
"""

from __future__ import annotations

from decimal import Decimal
import gzip
import io
import json
import tracemalloc

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from tests.support.environment.process import run_python
from unstd.serde import jsonx


class _BinarySink(io.RawIOBase):
    def __init__(self):
        super().__init__()
        self.output = bytearray()
        self.requests = []

    def write(self, data):
        self.requests.append(bytes(data))
        self.output.extend(data)
        return len(data)


class _TextSink(io.TextIOBase):
    def __init__(self):
        super().__init__()
        self.output = []
        self.requests = []

    def write(self, data):
        assert isinstance(data, str)
        self.requests.append(data)
        self.output.append(data)
        return len(data)


def _oracle(rows, **options):
    return json.dumps(
        rows, ensure_ascii=False, separators=(",", ":"), **options
    ).encode("utf-8")


@pytest.mark.parametrize("fallback", [False, True], ids=["installed", "stdlib"])
@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 64, 65536])
def test_exact_compact_output_with_every_write_bounded(
    fallback, binary, chunk_size, monkeypatch
):
    if fallback:
        monkeypatch.setattr(jsonx, "_HAVE_ORJSON", False)
        monkeypatch.setattr(jsonx, "_native_dumpb", None)
    rows = [
        None,
        True,
        False,
        0,
        -(2**160),
        -0.0,
        1.2345678901234567e-200,
        float("nan"),
        float("inf"),
        -float("inf"),
        'é界😀,][{}"\\\n\u0085\u2028\u2029',
        {"nested": [1, {"empty": []}], "other": {}},
        [],
    ]
    sink = _BinarySink() if binary else _TextSink()
    assert jsonx.dump_array(iter(rows), sink, chunk_size=chunk_size) == len(rows)
    actual = bytes(sink.output) if binary else "".join(sink.output).encode()
    assert actual == _oracle(rows)
    assert sink.requests[0] == (b"[" if binary else "[")
    assert all(0 < len(request) <= chunk_size for request in sink.requests)
    assert not sink.closed


@pytest.mark.parametrize("binary", [False, True])
def test_empty_source_writes_an_empty_array(binary):
    sink = io.BytesIO() if binary else io.StringIO()
    assert jsonx.dump_array(iter(()), sink, chunk_size=1) == 0
    assert sink.getvalue() == (b"[]" if binary else "[]")


@pytest.mark.parametrize("binary", [False, True])
def test_single_oversized_record_keeps_every_write_bounded(binary):
    text = 'é界😀,][{}"\\\n' * 20_000
    rows = [{"nested": [text, {"escaped": text[-113:]}], "id": 1}]
    assert len(text) >= 200_000
    sink = _BinarySink() if binary else _TextSink()
    assert jsonx.dump_array(iter(rows), sink, chunk_size=127) == 1
    actual = bytes(sink.output) if binary else "".join(sink.output).encode()
    assert actual == _oracle(rows)
    assert all(0 < len(request) <= 127 for request in sink.requests)
    assert not sink.closed


def test_opening_bracket_reaches_sink_before_source_is_consumed():
    sink = io.BytesIO()
    consumed = []

    def rows():
        assert sink.getvalue() == b"["
        for value in range(100):
            consumed.append(value)
            yield value

    assert jsonx.dump_array(rows(), sink, chunk_size=16) == 100
    assert consumed == list(range(100))
    assert sink.getvalue() == _oracle(list(range(100)))


def test_reused_mutable_row_is_encoded_before_producer_advances():
    row = {"value": None}

    def rows():
        for value in range(50):
            row["value"] = value
            yield row

    sink = io.BytesIO()
    assert jsonx.dump_array(rows(), sink) == 50
    assert sink.getvalue() == _oracle([{"value": value} for value in range(50)])


def test_one_shot_iterable_is_never_reiterated():
    class Once:
        def __init__(self):
            self.called = False

        def __iter__(self):
            assert not self.called
            self.called = True
            yield from (1, {"two": 2}, [3])

    sink = io.BytesIO()
    assert jsonx.dump_array(Once(), sink) == 3
    assert sink.getvalue() == b'[1,{"two":2},[3]]'


@pytest.mark.parametrize("binary", [False, True])
def test_sorting_and_default_follow_independent_stdlib_output(binary):
    rows = [{"z": Decimal("1.25"), "a": [Decimal("-0"), "é😀"]}]
    sink = io.BytesIO() if binary else io.StringIO()
    assert jsonx.dump_array(rows, sink, sort_keys=True, default=str) == 1
    actual = sink.getvalue() if binary else sink.getvalue().encode()
    assert actual == _oracle(rows, sort_keys=True, default=str)


def test_default_hooks_run_once_per_row_in_source_order():
    events = []

    class Value:
        def __init__(self, value):
            self.value = value

    def default(value):
        events.append(("encode", value.value))
        return {"value": value.value}

    def rows():
        for value in range(4):
            events.append(("produce", value))
            yield Value(value)

    sink = io.BytesIO()
    assert jsonx.dump_array(rows(), sink, default=default) == 4
    assert events == [
        event for value in range(4) for event in [("produce", value), ("encode", value)]
    ]
    assert sink.getvalue() == _oracle([{"value": value} for value in range(4)])


def test_numpy_conversion_matches_values_from_independent_stdlib():
    np = pytest.importorskip("numpy")
    rows = [np.array([[1, 2], [3, 4]], dtype=np.int64), np.float32(1.25)]
    sink = io.BytesIO()
    assert jsonx.dump_array(rows, sink, numpy=True) == 2
    assert sink.getvalue() == _oracle([[[1, 2], [3, 4]], 1.25])


@pytest.mark.parametrize("binary", [False, True])
def test_gzip_streams_round_trip_without_being_closed(tmp_path, binary):
    path = tmp_path / "events.json.gz"
    rows = [{"text": f"é😀{index}", "id": index} for index in range(40)]
    options = {} if binary else {"encoding": "utf-8", "newline": ""}
    with gzip.open(path, "wb" if binary else "wt", **options) as sink:
        assert jsonx.dump_array(iter(rows), sink, chunk_size=3) == len(rows)
        assert not sink.closed
    with gzip.open(path, "rb") as source:
        assert source.read() == _oracle(rows)


@pytest.mark.parametrize("chunk_size", [0, -1, -(2**100)])
def test_nonpositive_chunk_size_fails_before_output_or_iteration(chunk_size):
    sink = io.BytesIO()

    def rows():
        pytest.fail("invalid size must not consume the source")
        yield 1

    with pytest.raises(ValueError, match="positive"):
        jsonx.dump_array(rows(), sink, chunk_size=chunk_size)
    assert sink.getvalue() == b""


@pytest.mark.parametrize("chunk_size", [1.0, "1", None, object()])
def test_chunk_size_requires_integer_index_semantics(chunk_size):
    sink = io.BytesIO()
    with pytest.raises(TypeError):
        jsonx.dump_array([1], sink, chunk_size=chunk_size)
    assert sink.getvalue() == b""


def test_chunk_size_accepts_index_objects_and_true():
    class Size:
        def __index__(self):
            return 3

    for size in (Size(), True):
        sink = _BinarySink()
        assert jsonx.dump_array(["é😀"], sink, chunk_size=size) == 1
        assert bytes(sink.output) == _oracle(["é😀"])
        assert max(map(len, sink.requests)) <= (3 if isinstance(size, Size) else 1)


@pytest.mark.parametrize("binary", [False, True])
def test_short_writes_finish_a_chunk_before_another_source_pull(binary):
    consumed = []
    completed = []
    active = None
    history = []

    def write(data):
        nonlocal active
        history.append(data)
        if active is not None:
            remaining, prior_consumed = active
            assert data == remaining
            assert consumed == prior_consumed
        accepted = data[:2]
        completed.append(accepted)
        active = (data[2:], consumed.copy()) if len(data) > 2 else None
        return len(accepted)

    class Binary(io.RawIOBase):
        def write(self, data):
            return write(data)

    class Text(io.TextIOBase):
        def write(self, data):
            return write(data)

    def rows():
        for value in range(20):
            consumed.append(value)
            yield {"value": f"é😀{value}"}

    sink = Binary() if binary else Text()
    assert jsonx.dump_array(rows(), sink, chunk_size=13) == 20
    actual = b"".join(completed) if binary else "".join(completed).encode()
    assert actual == _oracle([{"value": f"é😀{value}"} for value in range(20)])
    assert all(0 < len(request) <= 13 for request in history)
    assert active is None
    assert not sink.closed


def test_generic_sink_none_acknowledges_complete_writes():
    output = bytearray()

    class Sink:
        def write(self, data):
            output.extend(data)

    rows = [{"text": "é😀"}, 2]
    assert jsonx.dump_array(rows, Sink(), chunk_size=3) == 2
    assert bytes(output) == _oracle(rows)


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("result", [None, 0])
def test_io_stream_without_progress_raises_before_consuming_rows(binary, result):
    attempts = []
    consumed = []

    def write(data):
        attempts.append(data)
        assert len(attempts) == 1, "a blocked sink must not be retried"
        return result

    class Binary(io.RawIOBase):
        def write(self, data):
            return write(data)

    class Text(io.TextIOBase):
        def write(self, data):
            return write(data)

    def rows():
        consumed.append(1)
        yield 1

    sink = Binary() if binary else Text()
    with pytest.raises(BlockingIOError):
        jsonx.dump_array(rows(), sink)
    assert consumed == []
    assert not sink.closed


@pytest.mark.parametrize("result", [-1, 2, True, False, 1.0, "1", object()])
def test_invalid_write_counts_are_io_errors(result):
    class Sink:
        def write(self, data):
            assert data == b"["
            return result

    with pytest.raises(OSError, match="write count"):
        jsonx.dump_array([1], Sink())


def test_sink_failure_preserves_exception_and_stops_source_before_exhaustion():
    failure = OSError("sink disconnected")
    consumed = []
    output = bytearray()
    attempts = 0

    class Sink:
        def write(self, data):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                output.extend(data)
                return len(data)
            if attempts == 2:
                output.extend(data[:2])
                return 2
            raise failure

    def rows():
        for value in range(1000):
            consumed.append(value)
            yield {"id": value}

    with pytest.raises(OSError, match="sink disconnected") as caught:
        jsonx.dump_array(rows(), Sink(), chunk_size=16)
    assert caught.value is failure
    assert 0 < len(consumed) < 1000
    assert output.startswith(b"[")
    assert not output.endswith(b"]")


@pytest.mark.parametrize("failure_kind", ["producer", "default"])
def test_producer_and_callback_errors_propagate_without_retry_or_closing_array(
    failure_kind,
):
    failure = RuntimeError("deliberate failure")
    events = []
    sink = io.BytesIO()

    def default(value):
        events.append("default")
        raise failure

    def rows():
        events.append("first")
        yield 1
        events.append("second")
        if failure_kind == "producer":
            raise failure
        yield object()
        events.append("third")
        yield 3

    with pytest.raises(RuntimeError, match="deliberate failure") as caught:
        jsonx.dump_array(rows(), sink, default=default, chunk_size=1)
    assert caught.value is failure
    assert events == (
        ["first", "second"]
        if failure_kind == "producer"
        else ["first", "second", "default"]
    )
    assert sink.getvalue().startswith(b"[")
    assert not sink.getvalue().endswith(b"]")
    assert not sink.closed


@pytest.mark.parametrize("binary", [False, True])
def test_unencodable_unicode_keeps_utf8_failure_even_for_text_sinks(binary):
    sink = io.BytesIO() if binary else io.StringIO()
    with pytest.raises(UnicodeEncodeError):
        jsonx.dump_array(["\ud800"], sink)
    assert not sink.closed


def test_writer_never_flushes_or_closes_caller_sink():
    class Sink:
        def __init__(self):
            self.output = bytearray()

        def write(self, data):
            self.output.extend(data)
            return len(data)

        def flush(self):
            pytest.fail("flush belongs to the caller")

        def close(self):
            pytest.fail("close belongs to the caller")

    sink = Sink()
    assert jsonx.dump_array([1, 2], sink) == 2
    assert bytes(sink.output) == b"[1,2]"


def test_peak_allocations_do_not_scale_with_generated_corpus():
    class Sink:
        def write(self, data):
            return len(data)

    def peak(count):
        def rows():
            for value in range(count):
                yield {"id": value, "text": f"{value:08d}" + "x" * 4088}

        tracemalloc.start()
        try:
            assert jsonx.dump_array(rows(), Sink(), chunk_size=1024) == count
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    jsonx.dump_array([{"warm": "up"}], Sink(), chunk_size=1024)
    small = peak(20)
    large = peak(2000)
    assert large < small + 512 * 1024


_SCALARS = st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False)
_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=30)
_JSON = st.recursive(
    _SCALARS | _TEXT,
    lambda values: (
        st.lists(values, max_size=5) | st.dictionaries(_TEXT, values, max_size=5)
    ),
    max_leaves=20,
)


@settings(max_examples=70, deadline=None)
@given(rows=st.lists(_JSON, max_size=10), chunk_size=st.integers(1, 90))
def test_generated_values_match_independent_compact_json(rows, chunk_size):
    sink = _BinarySink()
    assert jsonx.dump_array(iter(rows), sink, chunk_size=chunk_size) == len(rows)
    assert bytes(sink.output) == _oracle(rows)
    assert all(0 < len(request) <= chunk_size for request in sink.requests)


def test_base_install_streams_without_optional_backends(tmp_path):
    code = """
import importlib.abc
import io
import json
import sys

class Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'orjson', 'msgspec', 'numpy'} or fullname == 'unstd.serde._json_native':
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, Absent())
from unstd.serde import jsonx
assert not jsonx._HAVE_MSGSPEC and not jsonx._HAVE_ORJSON and not jsonx._HAVE_NUMPY
assert jsonx._native is None
rows = [{'text': 'café😀', 'nested': [1, None]}, True, 2**160, -0.0]
expected = json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()
for binary in (True, False):
    sink = io.BytesIO() if binary else io.StringIO()
    assert jsonx.dump_array(iter(rows), sink, chunk_size=1) == len(rows)
    actual = sink.getvalue() if binary else sink.getvalue().encode()
    assert actual == expected
    assert not sink.closed
"""
    result = run_python(code, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
