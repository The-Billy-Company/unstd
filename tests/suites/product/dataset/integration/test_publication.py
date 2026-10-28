"""Failed imports preserve published snapshots and existing readers retain them."""

from __future__ import annotations

import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys

import pytest

from unstd.dataset import Dataset, build
from unstd.serde import ndjson


def _publish_or_check_os_refusal(path, rows):
    previous = path.read_bytes()
    entries = set(path.parent.iterdir())
    failure = None
    try:
        assert build(path, rows) == len(rows)
    except OSError as error:
        if sys.platform != "win32":
            raise
        failure = error
    if failure is not None:
        assert failure.winerror in {5, 32, 33}
        assert path.read_bytes() == previous
        assert set(path.parent.iterdir()) == entries
        return False
    return True


def test_open_reader_retains_its_snapshot_after_atomic_replacement(tmp_path):
    path = tmp_path / "rows.dataset"
    before = [{"version": "before", "id": index} for index in range(5)]
    after = [{"version": "after", "id": index} for index in range(3)]
    build(path, before)
    with Dataset(path) as original:
        assert original[0] == before[0]
        replaced = _publish_or_check_os_refusal(path, after)
        assert len(original) == 5
        assert list(original) == before
        with Dataset(path) as published:
            expected = after if replaced else before
            assert len(published) == len(expected)
            assert list(published) == expected


def test_reader_opened_before_replacement_without_row_reads_keeps_snapshot(tmp_path):
    path = tmp_path / "rows.dataset"
    build(path, ["original"])
    with Dataset(path) as original:
        _publish_or_check_os_refusal(path, ["replacement", "second"])
        assert len(original) == 1
        assert original[0] == "original"


@pytest.mark.parametrize("failure", ["producer", "encode", "oversized", "interrupt"])
def test_failed_build_preserves_the_exact_previous_artifact(tmp_path, failure):
    path = tmp_path / "rows.dataset"
    build(path, [{"published": True}])
    digest = sha256(path.read_bytes()).digest()
    initial_entries = set(tmp_path.iterdir())

    def producer():
        yield {"unpublished": 1}
        if failure == "producer":
            message = "source failed"
            raise RuntimeError(message)
        if failure == "encode":
            yield object()
        elif failure == "oversized":
            yield "x" * 100
        else:
            raise KeyboardInterrupt

    expected = {
        "producer": RuntimeError,
        "encode": TypeError,
        "oversized": ValueError,
        "interrupt": KeyboardInterrupt,
    }[failure]
    with pytest.raises(expected):
        build(path, producer(), max_row_bytes=32)
    assert sha256(path.read_bytes()).digest() == digest
    assert set(tmp_path.iterdir()) == initial_entries
    with Dataset(path) as dataset:
        assert list(dataset) == [{"published": True}]


def test_interruption_at_final_publication_preserves_previous_bytes(
    tmp_path, monkeypatch
):
    path = tmp_path / "rows.dataset"
    build(path, [{"published": True}])
    prior = path.read_bytes()
    initial_entries = set(tmp_path.iterdir())
    attempts = []

    def interrupted_replace(source, target):
        assert Path(target) == path
        assert source.read_bytes().startswith(b"UNSTDDS\x02")
        attempts.append(source)
        raise KeyboardInterrupt

    monkeypatch.setattr(Path, "replace", interrupted_replace)
    with pytest.raises(KeyboardInterrupt):
        build(path, [{"unpublished": True}])
    assert len(attempts) == 1
    assert path.read_bytes() == prior
    assert set(tmp_path.iterdir()) == initial_entries
    with Dataset(path) as dataset:
        assert list(dataset) == [{"published": True}]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission-bit contract")
def test_new_artifact_is_private_and_rebuild_preserves_existing_permissions(tmp_path):
    path = tmp_path / "rows.dataset"
    build(path, [1])
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    path.chmod(0o640)
    build(path, [2])
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    with Dataset(path) as dataset:
        assert dataset[0] == 2


def test_gzip_ndjson_import_is_independent_of_original_source(tmp_path):
    source = tmp_path / "source.jsonl.gz"
    destination = tmp_path / "indexed.dataset"
    expected = [{"id": index, "label": "東京"} for index in range(40)]
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        stream.write("\n")
        for row in expected:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    with gzip.open(source, "rb") as stream:
        assert build(destination, ndjson.iter_loads(stream)) == 40
    source.unlink()
    with Dataset(destination) as dataset:
        assert dataset[37] == expected[37]
        assert dataset[-3:] == expected[-3:]
        assert list(dataset) == expected


def test_malformed_ndjson_aborts_import_without_publishing(tmp_path):
    destination = tmp_path / "indexed.dataset"
    source = [b'{"valid": 1}\n', b"{invalid JSON}\n", b'{"valid": 2}\n']
    with pytest.raises(ndjson.NDJSONDecodeError) as caught:
        build(destination, ndjson.iter_loads(source))
    assert caught.value.lineno == 2
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_dataset_path_with_uri_metacharacters_is_literal(tmp_path):
    # Windows forbids '?' in filenames; '#', '%' and '=' remain literal there.
    name = (
        "what%3Fmode=rw#東京.dataset"
        if os.name == "nt"
        else "what?mode=rw#東京.dataset"
    )
    path = tmp_path / name
    build(str(path), [{"literal": True}])
    with Dataset(path) as dataset:
        assert dataset[0] == {"literal": True}
    assert [entry.name for entry in tmp_path.iterdir()] == [path.name]


def test_partial_iteration_can_be_abandoned_before_reader_closes(tmp_path):
    path = tmp_path / "rows.dataset"
    build(path, range(20))
    with Dataset(path) as dataset:
        iterator = iter(dataset)
        assert next(iterator) == 0
        assert dataset[19] == 19
    with pytest.raises(ValueError, match="closed"):
        next(iterator)


def test_files_can_be_removed_after_dataset_close(tmp_path):
    path = tmp_path / "rows.dataset"
    build(path, [1, 2, 3])
    with Dataset(path) as dataset:
        assert dataset[1] == 2
    path.unlink()
    assert not Path(path).exists()
