"""Bulk and lazy dataset access follow independent list models and decode budgets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from itertools import islice

import pytest

from unstd.dataset import Dataset, build
from unstd.serde import jsonx, ndjson


@pytest.fixture
def artifact(tmp_path):
    path = tmp_path / "rows.dataset"
    rows = [{"id": index, "items": [index, None]} for index in range(31)]
    build(path, rows)
    return path, rows


@pytest.mark.parametrize(
    "indices", [[], [0], [30, 0, 17, 2], [-1, -31], [4, 4, -27], range(30, -1, -3)]
)
def test_take_preserves_requested_order_duplicates_and_negative_indices(
    artifact, indices
):
    path, rows = artifact
    with Dataset(path) as dataset:
        assert dataset.take(iter(indices)) == [rows[index] for index in indices]


def test_take_decodes_duplicate_rows_independently(artifact):
    path, rows = artifact
    with Dataset(path) as dataset:
        values = dataset.take([7, 7])
        assert values == [rows[7], rows[7]]
        assert values[0] is not values[1]
        assert values[0]["items"] is not values[1]["items"]
        values[0]["items"].append("changed")
        assert values[1] == rows[7]
        assert dataset[7] == rows[7]


@pytest.mark.parametrize(
    "indices", [[0, 31], [1, -32], [2, 10**100], [0, 1.5], [0, "2"]]
)
def test_take_validates_entire_selection_before_decoding(
    artifact, monkeypatch, indices
):
    path, _ = artifact

    def never_decode(*_args, **_kwargs):
        pytest.fail("an invalid selection decoded a partial result")

    monkeypatch.setattr(jsonx, "loads", never_decode)
    with Dataset(path) as dataset, pytest.raises((IndexError, TypeError)):
        dataset.take(indices)


def test_take_source_failure_precedes_any_decoding(artifact, monkeypatch):
    path, _ = artifact

    def selections():
        yield 0
        message = "selection source failed"
        raise RuntimeError(message)

    def never_decode(*_args, **_kwargs):
        pytest.fail("failed selection source decoded a partial result")

    monkeypatch.setattr(jsonx, "loads", never_decode)
    with (
        Dataset(path) as dataset,
        pytest.raises(RuntimeError, match="selection source"),
    ):
        dataset.take(selections())


@pytest.mark.parametrize(
    ("start", "stop", "step"),
    [
        (None, None, 1),
        (2, 17, 3),
        (-9, None, 2),
        (None, None, -1),
        (24, 3, -4),
        (1000, -1000, -2),
        (1000, 2000, 1),
        (None, None, 10**30),
    ],
)
def test_iter_rows_matches_python_slices(artifact, start, stop, step):
    path, rows = artifact
    with Dataset(path) as dataset:
        assert (
            list(dataset.iter_rows(start, stop, step)) == rows[slice(start, stop, step)]
        )


def test_iter_rows_is_lazy_and_decodes_only_consumed_values(artifact, monkeypatch):
    path, rows = artifact
    decode = jsonx.loads
    calls = []

    def observed(raw, *args, **kwargs):
        calls.append(raw)
        return decode(raw, *args, **kwargs)

    monkeypatch.setattr(jsonx, "loads", observed)
    with Dataset(path) as dataset:
        selected = dataset.iter_rows(3, 25, 4)
        assert calls == []
        assert next(selected) == rows[3]
        assert len(calls) == 1
        assert list(islice(selected, 2)) == [rows[7], rows[11]]
        assert len(calls) == 3
        selected.close()
        assert len(calls) == 3


def test_zero_iter_rows_step_is_rejected(artifact):
    path, _ = artifact
    with Dataset(path) as dataset, pytest.raises(ValueError, match="step"):
        list(dataset.iter_rows(step=0))


@pytest.mark.parametrize("size", [1, 4, 100])
@pytest.mark.parametrize(
    ("start", "stop"), [(0, None), (2, 23), (-7, -1), (100, 200), (20, 4)]
)
def test_batches_are_independently_modeled_chunks(artifact, size, start, stop):
    path, rows = artifact
    selection = rows[slice(start, stop)]
    expected = [
        selection[index : index + size] for index in range(0, len(selection), size)
    ]
    with Dataset(path) as dataset:
        assert list(dataset.batches(size, start=start, stop=stop)) == expected


@pytest.mark.parametrize("size", [0, -1, True, False, 1.5, "4", None])
def test_batches_reject_invalid_sizes(artifact, size):
    path, _ = artifact
    with Dataset(path) as dataset, pytest.raises((TypeError, ValueError)):
        list(dataset.batches(size))


def test_batches_do_not_decode_beyond_the_current_batch(artifact, monkeypatch):
    path, rows = artifact
    decode = ndjson.loads
    calls = []

    def observed(raw, *args, **kwargs):
        calls.extend(raw.splitlines())
        return decode(raw, *args, **kwargs)

    monkeypatch.setattr(ndjson, "loads", observed)
    with Dataset(path) as dataset:
        chunks = dataset.batches(4, start=5, stop=15)
        assert calls == []
        assert next(chunks) == rows[5:9]
        assert len(calls) == 4
        assert next(chunks) == rows[9:13]
        assert len(calls) == 8
        assert next(chunks) == rows[13:15]
        assert len(calls) == 10
        with pytest.raises(StopIteration):
            next(chunks)
        assert len(calls) == 10


def test_interleaved_iterators_have_independent_positions(artifact):
    path, rows = artifact
    with Dataset(path) as dataset:
        first = dataset.iter_rows(1, 20, 2)
        second = iter(dataset)
        third = dataset.iter_rows(25, 0, -3)
        assert next(first) == rows[1]
        assert next(second) == rows[0]
        assert next(third) == rows[25]
        assert next(first) == rows[3]
        assert next(second) == rows[1]
        assert list(third) == rows[22:0:-3]
        assert list(first) == rows[5:20:2]
        assert list(second) == rows[2:]


def test_readonly_handle_supports_multiple_threads_with_independent_reads(artifact):
    path, rows = artifact
    selections = ([30, 0, 17, 2], [4, 4, -1], list(range(7, 24)), [])
    with Dataset(path) as dataset, ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(dataset.take, selections))
        scans = list(
            workers.map(lambda index: list(dataset.iter_rows(index, None, 4)), range(4))
        )
        assert results == [[rows[index] for index in indices] for indices in selections]
        assert scans == [rows[index::4] for index in range(4)]
        assert dataset.verify() is None


def test_all_new_operations_reject_closed_handles(artifact):
    path, _ = artifact
    dataset = Dataset(path)
    dataset.close()
    for operation in (
        lambda: dataset.take([]),
        lambda: dataset.take([0]),
        lambda: list(dataset.iter_rows()),
        lambda: list(dataset.batches()),
        dataset.verify,
    ):
        with pytest.raises(ValueError, match="closed"):
            operation()


def test_batch_iterator_releases_previous_batch_before_decoding_another(
    artifact, monkeypatch
):
    import gc
    import weakref

    path, rows = artifact
    decode = ndjson.loads
    previous = []

    class TrackedRows(list):
        pass

    def observed(raw, *args, **kwargs):
        gc.collect()
        if previous:
            assert previous[-1]() is None, (
                "iterator retained the previous decoded batch"
            )
        result = TrackedRows(decode(raw, *args, **kwargs))
        previous.append(weakref.ref(result))
        return result

    monkeypatch.setattr(ndjson, "loads", observed)
    with Dataset(path) as dataset:
        chunks = dataset.batches(4, start=5, stop=15)
        first = next(chunks)
        assert first == rows[5:9]
        del first
        second = next(chunks)
        assert second == rows[9:13]
        del second
        final = next(chunks)
        assert final == rows[13:15]
        chunks.close()
