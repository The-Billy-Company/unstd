"""Indexed JSON snapshots preserve sequence laws without eager row decoding."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from itertools import islice
import json
import struct

import pytest

from unstd.dataset import Dataset, build
from unstd.serde import jsonx, ndjson


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [None, False, True, 0, -7, 1.25, "", "café\ud800"],
        [{"id": 1, "items": [None, True]}, [], {}, "東京 🎹"],
    ],
)
def test_values_survive_reopening_as_a_sequence(tmp_path, rows):
    path = tmp_path / "rows.dataset"
    assert build(path, iter(rows)) == len(rows)
    with Dataset(str(path)) as dataset:
        assert isinstance(dataset, Sequence)
        assert len(dataset) == len(rows)
        assert list(dataset) == rows
    with Dataset(path) as reopened:
        assert reopened[:] == rows


def test_missing_parent_is_created_and_empty_dataset_is_valid(tmp_path):
    path = tmp_path / "new" / "directory" / "empty.dataset"
    assert build(path, []) == 0
    with Dataset(path) as dataset:
        assert len(dataset) == 0
        assert list(dataset) == []
        assert dataset[:] == []
        with pytest.raises(IndexError):
            _ = dataset[0]


def test_json_normalization_is_independently_defined_by_stdlib(tmp_path):
    rows = [{1: ("value", 2)}, (3, 4)]
    expected = json.loads(json.dumps(rows, allow_nan=False))
    path = tmp_path / "normalized.dataset"
    build(path, rows)
    with Dataset(path) as dataset:
        assert list(dataset) == expected


@pytest.mark.parametrize("index", [-9, -2, -1, 0, 1, 8, False, True])
def test_integer_access_matches_python_list(tmp_path, index):
    rows = [{"id": index} for index in range(9)]
    path = tmp_path / "rows.dataset"
    build(path, rows)
    with Dataset(path) as dataset:
        assert dataset[index] == rows[index]


@pytest.mark.parametrize("index", [-10, 9, 10**50, -(10**50)])
def test_out_of_range_indices_raise_index_error(tmp_path, index):
    path = tmp_path / "rows.dataset"
    build(path, range(9))
    with Dataset(path) as dataset, pytest.raises(IndexError):
        _ = dataset[index]


@pytest.mark.parametrize("index", [1.5, "1", None, object()])
def test_invalid_index_types_raise_type_error(tmp_path, index):
    path = tmp_path / "rows.dataset"
    build(path, [0, 1])
    with Dataset(path) as dataset, pytest.raises(TypeError):
        _ = dataset[index]


@pytest.mark.parametrize(
    "selection",
    [
        slice(None),
        slice(2, 7),
        slice(-4, -1),
        slice(None, None, -1),
        slice(None, None, 2),
        slice(7, 1, -2),
        slice(-100, 100, 3),
        slice(100, -100, -3),
        slice(2, 2),
        slice(8, 2),
        slice(100, 200),
        slice(-(10**50), 10**50),
        slice(10**50, 10**50 + 1),
        slice(None, None, 10**50),
        slice(None, None, -(10**50)),
    ],
)
def test_slice_access_matches_python_list(tmp_path, selection):
    rows = [{"id": index} for index in range(9)]
    path = tmp_path / "rows.dataset"
    build(path, rows)
    with Dataset(path) as dataset:
        assert dataset[selection] == rows[selection]


def test_zero_slice_step_raises(tmp_path):
    path = tmp_path / "rows.dataset"
    build(path, [1, 2])
    with Dataset(path) as dataset, pytest.raises(ValueError, match="slice step"):
        _ = dataset[::0]


def test_each_access_returns_independent_json_values(tmp_path):
    path = tmp_path / "rows.dataset"
    original = {"items": [{"name": "original"}]}
    build(path, [original])
    original["items"][0]["name"] = "source mutated"
    with Dataset(path) as dataset:
        first = dataset[0]
        first["items"][0]["name"] = "read mutated"
        assert dataset[0] == {"items": [{"name": "original"}]}


def test_length_and_indexed_reads_decode_only_requested_rows(tmp_path, monkeypatch):
    path = tmp_path / "rows.dataset"
    rows = [{"id": index} for index in range(100)]
    build(path, rows)
    decode = jsonx.loads
    calls = []

    def observed(raw, *args, **kwargs):
        calls.append(raw)
        return decode(raw, *args, **kwargs)

    monkeypatch.setattr(jsonx, "loads", observed)
    with Dataset(path) as dataset:
        assert len(dataset) == 100
        assert calls == []
        assert dataset[73] == {"id": 73}
        assert len(calls) == 1
        assert dataset[3:6] == rows[3:6]
        assert len(calls) == 4
        assert list(islice(dataset, 3)) == rows[:3]
        assert len(calls) == 7


def test_build_consumes_and_encodes_rows_before_advancing(tmp_path):
    path = tmp_path / "rows.dataset"
    reused = {"id": -1}

    def producer():
        for index in range(32):
            reused["id"] = index
            yield reused

    assert build(path, producer()) == 32
    with Dataset(path) as dataset:
        assert list(dataset) == [{"id": index} for index in range(32)]


def test_ndjson_reader_composes_without_losing_blank_line_policy(tmp_path):
    path = tmp_path / "rows.dataset"
    source = [b"\n", b'{"id":1}\n', b" \t\r\n", b"null\n", b"[2,3]\n"]
    assert build(path, ndjson.iter_loads(source)) == 3
    with Dataset(path) as dataset:
        assert list(dataset) == [{"id": 1}, None, [2, 3]]


@pytest.mark.parametrize("row", [float("nan"), float("inf"), {"x": -float("inf")}])
def test_nonfinite_rows_are_rejected(tmp_path, row):
    path = tmp_path / "invalid.dataset"
    with pytest.raises(ValueError, match=r"JSON compliant|range|finite"):
        build(path, [row])
    assert not path.exists()


@pytest.mark.parametrize("maximum", [0, -1, True, False, 1.5, "32", None])
def test_invalid_row_limit_is_rejected_before_input_consumption(tmp_path, maximum):
    def never_consumed():
        pytest.fail("invalid configuration consumed the source")
        yield None

    with pytest.raises((TypeError, ValueError)):
        build(tmp_path / "invalid.dataset", never_consumed(), max_row_bytes=maximum)


def test_row_byte_limit_includes_json_quoting_and_accepts_exact_boundary(tmp_path):
    path = tmp_path / "rows.dataset"
    assert build(path, ["abc"], max_row_bytes=5) == 1
    with Dataset(path) as dataset:
        assert dataset[0] == "abc"
    with pytest.raises(ValueError, match=r"row|bytes|limit"):
        build(path, ["abc"], max_row_bytes=4)
    with Dataset(path) as dataset:
        assert list(dataset) == ["abc"]


def test_close_is_idempotent_and_reads_fail_after_close(tmp_path):
    path = tmp_path / "rows.dataset"
    build(path, [{"id": 1}])
    dataset = Dataset(path)
    dataset.close()
    dataset.close()
    for operation in (
        lambda: len(dataset),
        lambda: dataset[0],
        lambda: dataset[:0],
        lambda: next(iter(dataset)),
    ):
        with pytest.raises(ValueError, match="closed"):
            operation()


def test_context_closes_dataset_when_body_raises(tmp_path):
    path = tmp_path / "rows.dataset"
    build(path, [1])
    message = "body failed"
    with pytest.raises(RuntimeError, match="body failed"), Dataset(path) as dataset:
        raise RuntimeError(message)
    with pytest.raises(ValueError, match="closed"):
        _ = dataset[0]


_HEADER = struct.Struct("<8sQQ32s")
_MAGIC = b"UNSTDDS\x02"


def _artifact(payloads):
    """An independent writer for the documented binary geometry."""
    offsets = [_HEADER.size]
    for payload in payloads:
        offsets.append(offsets[-1] + len(payload) + 1)
    index_offset = offsets[-1]
    body = b"".join(payload + b"\n" for payload in payloads)
    body += struct.pack(f"<{len(offsets)}Q", *offsets)
    return (
        _HEADER.pack(_MAGIC, len(payloads), index_offset, sha256(body).digest()) + body
    )


def test_independently_encoded_artifact_is_readable_and_verifies(tmp_path):
    path = tmp_path / "independent.dataset"
    path.write_bytes(_artifact([b"null", b'{"a":1}', b'"caf\\u00e9"']))
    with Dataset(path) as dataset:
        assert len(dataset) == 3
        assert list(dataset) == [None, {"a": 1}, "café"]
        assert dataset.verify() is None


def test_builder_emits_independently_expected_bytes(tmp_path):
    path = tmp_path / "portable.dataset"
    build(path, [None, {"a": 1}, "café"])
    assert path.read_bytes() == _artifact([b"null", b'{"a":1}', b'"caf\\u00e9"'])


@pytest.mark.parametrize(
    "payload", [b"", b"not an indexed dataset", b"SQLite format 3\x00"]
)
def test_invalid_artifact_is_refused(tmp_path, payload):
    path = tmp_path / "corrupt.dataset"
    path.write_bytes(payload)
    with pytest.raises(
        ValueError,
        match=r"dataset|snapshot|format|offset|row|checksum|digest|index|newline|integrity|header|magic|version|line",
    ):
        Dataset(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "magic",
        "version",
        "count-zero",
        "count-huge",
        "index-small",
        "index-huge",
        "first-offset",
        "last-offset",
        "truncated-header",
        "truncated-table",
        "trailing",
    ],
)
def test_format_and_endpoint_corruption_is_rejected_without_decoding(
    tmp_path, monkeypatch, mutation
):
    path = tmp_path / "corrupt.dataset"
    raw = bytearray(_artifact([b'"first"', b'"middle"', b'"last"']))
    _, count, index_offset, digest = _HEADER.unpack_from(raw)
    if mutation == "magic":
        raw[0] ^= 1
    elif mutation == "version":
        raw[7] ^= 1
    elif mutation == "count-zero":
        _HEADER.pack_into(raw, 0, _MAGIC, 0, index_offset, digest)
    elif mutation == "count-huge":
        _HEADER.pack_into(raw, 0, _MAGIC, 2**64 - 1, index_offset, digest)
    elif mutation == "index-small":
        _HEADER.pack_into(raw, 0, _MAGIC, count, _HEADER.size - 1, digest)
    elif mutation == "index-huge":
        _HEADER.pack_into(raw, 0, _MAGIC, count, 2**64 - 1, digest)
    elif mutation == "first-offset":
        struct.pack_into("<Q", raw, index_offset, _HEADER.size + 1)
    elif mutation == "last-offset":
        struct.pack_into("<Q", raw, index_offset + count * 8, index_offset - 1)
    elif mutation == "truncated-header":
        del raw[12:]
    elif mutation == "truncated-table":
        del raw[-1:]
    else:
        raw.append(0)
    path.write_bytes(raw)

    def never_decode(*_args, **_kwargs):
        pytest.fail("opening a corrupt dataset decoded row payloads")

    monkeypatch.setattr(jsonx, "loads", never_decode)
    with pytest.raises(
        ValueError,
        match=r"dataset|snapshot|format|offset|row|checksum|digest|index|newline|integrity|header|magic|version|line",
    ):
        Dataset(path)


@pytest.mark.parametrize("offset", [0, 2**64 - 1, _HEADER.size, _HEADER.size + 1])
def test_bad_middle_offset_is_detected_at_read_and_verification(tmp_path, offset):
    path = tmp_path / "corrupt.dataset"
    raw = bytearray(_artifact([b"0", b"1", b"2", b"3", b"4"]))
    _, _, index_offset, _ = _HEADER.unpack_from(raw)
    struct.pack_into("<Q", raw, index_offset + 2 * 8, offset)
    path.write_bytes(raw)
    with Dataset(path) as dataset:
        assert len(dataset) == 5
        assert dataset[0] == 0
        assert dataset[-1] == 4
        for read in (lambda: dataset[2], lambda: dataset[1:4], dataset.verify):
            with pytest.raises(
                ValueError,
                match=r"dataset|snapshot|format|offset|row|checksum|digest|index|newline|integrity|header|magic|version|line",
            ):
                read()


def test_missing_row_newline_is_rejected(tmp_path):
    path = tmp_path / "corrupt.dataset"
    raw = bytearray(_artifact([b"0", b"1", b"2"]))
    raw[_HEADER.size + 3] = ord("x")
    path.write_bytes(raw)
    with Dataset(path) as dataset:
        assert dataset[0] == 0
        with pytest.raises(
            ValueError,
            match=r"dataset|snapshot|format|offset|row|checksum|digest|index|newline|integrity|header|magic|version|line",
        ):
            _ = dataset[1]
        with pytest.raises(
            ValueError,
            match=r"dataset|snapshot|format|offset|row|checksum|digest|index|newline|integrity|header|magic|version|line",
        ):
            dataset.verify()


@pytest.mark.parametrize("payload", [b"{invalid", b"[1,", b"\xff"])
def test_malformed_payload_is_reported_only_when_requested(tmp_path, payload):
    path = tmp_path / "corrupt.dataset"
    raw = bytearray(_artifact([b'{"id":0}', b'{"id":1}', b'{"id":2}']))
    _, _, index_offset, _ = _HEADER.unpack_from(raw)
    begin, end = struct.unpack_from("<QQ", raw, index_offset + 8)
    raw[begin : end - 1] = payload.ljust(end - begin - 1, b" ")
    path.write_bytes(raw)
    with Dataset(path) as dataset:
        assert dataset[0] == {"id": 0}
        assert dataset[-1] == {"id": 2}
        with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
            _ = dataset[1]
        with pytest.raises(
            ValueError,
            match=r"dataset|snapshot|format|offset|row|checksum|digest|index|newline|integrity|header|magic|version|line",
        ):
            dataset.verify()


def test_verification_detects_valid_json_tampering_without_decoding(
    tmp_path, monkeypatch
):
    path = tmp_path / "changed.dataset"
    raw = bytearray(_artifact([b"0", b"1", b"2"]))
    raw[_HEADER.size] = ord("9")
    path.write_bytes(raw)
    with Dataset(path) as dataset:
        assert dataset[0] == 9

        def never_decode(*_args, **_kwargs):
            pytest.fail("integrity verification decoded JSON")

        monkeypatch.setattr(jsonx, "loads", never_decode)
        with pytest.raises(
            ValueError,
            match=r"dataset|snapshot|format|offset|row|checksum|digest|index|newline|integrity|header|magic|version|line",
        ):
            dataset.verify()


def test_successful_verification_decodes_no_json(tmp_path, monkeypatch):
    path = tmp_path / "valid.dataset"
    build(path, [{"id": index} for index in range(32)])

    def never_decode(*_args, **_kwargs):
        pytest.fail("integrity verification decoded JSON")

    monkeypatch.setattr(jsonx, "loads", never_decode)
    with Dataset(path) as dataset:
        assert dataset.verify() is None


@pytest.mark.parametrize("selection", [slice(10**50), slice(None, None, -(10**50))])
def test_empty_dataset_slices_with_huge_indices_are_empty(tmp_path, selection):
    path = tmp_path / "empty.dataset"
    build(path, [])
    with Dataset(path) as dataset:
        assert dataset[selection] == []


def test_missing_dataset_is_not_silently_created(tmp_path):
    path = tmp_path / "absent.dataset"
    with pytest.raises(FileNotFoundError):
        Dataset(path)
    assert not path.exists()


def test_batches_refuse_extra_jsonl_records_hidden_inside_one_indexed_row(tmp_path):
    path = tmp_path / "bad-framing.dataset"
    path.write_bytes(_artifact([b"0\n1", b"2"]))
    with Dataset(path) as dataset:
        assert dataset[1] == 2
        with pytest.raises(ValueError, match=r"row count|dataset"):
            list(dataset.batches(2))
