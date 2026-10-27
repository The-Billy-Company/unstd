"""Mapping cleanup is explicit across retained views and descriptor-close failures."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from unstd.dataset import Dataset, build
import unstd.dataset.storage as storage


@pytest.fixture(params=["python", "native"])
def backend(request, monkeypatch):
    native = (
        None
        if request.param == "python"
        else pytest.importorskip("unstd.dataset._dataset_native")
    )
    monkeypatch.setattr(storage, "_native", native)
    return native


def test_verify_releases_retained_child_views_without_refcount_cleanup(
    tmp_path, monkeypatch, backend
):
    del backend
    path = tmp_path / "rows.dataset"
    build(path, ({"id": index, "text": "x" * 8192} for index in range(140)))
    retained = []
    real_memoryview = memoryview

    class RetainingView:
        def __init__(self, source):
            self.view = real_memoryview(source)

        def __enter__(self):
            return self

        def __exit__(self, *_error):
            self.view.release()

        def __getitem__(self, key):
            child = self.view[key]
            retained.append(child)
            return child

    monkeypatch.setattr(storage, "memoryview", RetainingView, raising=False)
    with Dataset(path) as dataset:
        assert dataset.verify() is None
        # Retained child objects simulate an interpreter that does not free
        # temporary views immediately. Explicit view release must suffice.
        assert len(retained) >= 2
        dataset.close()
        for child in retained:
            with pytest.raises(ValueError, match="released"):
                len(child)


def test_mapping_closes_when_source_context_exit_raises_after_file_close(
    tmp_path, monkeypatch, backend
):
    path = tmp_path / "rows.dataset"
    build(path, [None, {"ok": True}])
    mappings = []
    streams = []
    failure = OSError("source descriptor context exit failed")
    real_open = Path.open
    real_mmap = storage.mmap.mmap

    def tracked_mmap(*args, **kwargs):
        mapped = real_mmap(*args, **kwargs)
        mappings.append(mapped)
        return mapped

    monkeypatch.setattr(
        storage,
        "mmap",
        SimpleNamespace(mmap=tracked_mmap, ACCESS_READ=storage.mmap.ACCESS_READ),
    )
    if backend is not None:
        real_map = backend.map

        def tracked_native_map(*args, **kwargs):
            mapped = real_map(*args, **kwargs)
            if mapped is not None:
                mappings.append(mapped)
            return mapped

        monkeypatch.setattr(backend, "map", tracked_native_map)

    class FailedClose:
        def __init__(self, source, *args, **kwargs):
            self.stream = real_open(source, *args, **kwargs)
            streams.append(self.stream)

        def __enter__(self):
            return self.stream

        def __exit__(self, *_error):
            self.stream.close()
            raise failure

    def altered_open(source, *args, **kwargs):
        if source == path:
            return FailedClose(source, *args, **kwargs)
        return real_open(source, *args, **kwargs)

    monkeypatch.setattr(Path, "open", altered_open)
    with pytest.raises(OSError, match="descriptor context exit") as caught:
        Dataset(path)
    assert caught.value is failure
    assert len(streams) == len(mappings) == 1
    assert streams[0].closed
    mapped = mappings[0]
    for access in (lambda: len(mapped), lambda: memoryview(mapped)):
        with pytest.raises(ValueError, match="closed"):
            access()
