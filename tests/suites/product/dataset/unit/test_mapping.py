"""Native read-only mappings own resources independently of source file handles."""

from __future__ import annotations

import pytest

from tests.support.environment.process import run_python


@pytest.fixture
def mapped(tmp_path):
    native = pytest.importorskip("unstd.dataset._dataset_native")
    path = tmp_path / "source.bin"
    expected = b"0123\nhello\nlast\n"
    path.write_bytes(expected)
    with path.open("rb") as stream:
        owner = native.map(stream.fileno())
    if owner is None:
        pytest.skip("native read-only mapping is unavailable on this platform")
    try:
        yield native, owner, expected
    finally:
        owner.close()


def test_mapping_survives_closing_its_source_handle(mapped):
    _, owner, expected = mapped
    assert len(owner) == len(expected)
    assert owner[:] == expected
    assert owner.find(b"\n", 0, len(owner)) == expected.find(b"\n")
    assert owner.find(b"\n", 5, len(owner)) == expected.find(b"\n", 5)
    assert owner.find(b"x", 0, len(owner)) == -1


@pytest.mark.parametrize(
    "index",
    [
        0,
        4,
        -1,
        -15,
        slice(None),
        slice(2, 9),
        slice(None, None, -1),
        slice(1, 13, 3),
        slice(13, 1, -4),
        slice(-(10**100), 10**100),
    ],
)
def test_native_mapping_indexing_and_slices_match_bytes(mapped, index):
    _, owner, expected = mapped
    assert owner[index] == expected[index]


def test_mapping_exports_readonly_buffers(mapped):
    _, owner, expected = mapped
    with memoryview(owner) as view:
        assert view.readonly
        assert view.tobytes() == expected
        with pytest.raises(TypeError):
            view[0] = 0


def test_exported_buffer_prevents_close_without_invalidating_owner(mapped):
    _, owner, expected = mapped
    with memoryview(owner) as view:
        with pytest.raises(BufferError):
            owner.close()
        assert len(owner) == len(expected)
        assert view.tobytes() == expected
    owner.close()
    owner.close()
    with pytest.raises(ValueError, match="closed"):
        len(owner)


def test_owned_copies_remain_valid_after_mapping_closes(mapped):
    _, owner, expected = mapped
    copied = owner[:]
    assert isinstance(copied, bytes)
    owner.close()
    assert copied == expected
    assert copied[:5] == b"0123\n"


def test_closed_mapping_rejects_access_and_buffer_requests(mapped):
    native, owner, _ = mapped
    owner.close()
    for operation in (
        lambda: len(owner),
        lambda: owner[0],
        lambda: owner[:],
        lambda: owner.find(b"x", 0, 0),
        lambda: memoryview(owner),
        lambda: native.validate(owner, 56, 0),
    ):
        with pytest.raises(ValueError, match="closed"):
            operation()


def test_mapping_type_requires_the_owned_factory(mapped):
    _, owner, _ = mapped
    with pytest.raises(TypeError):
        type(owner)()


def test_mapping_failure_cleanup_and_export_lifetime_in_bounded_worker(
    tmp_path, mapped
):
    del mapped
    code = r"""
import gc
import os
from pathlib import Path
from unstd.dataset import _dataset_native as native
source=Path("mapping.bin")
source.write_bytes(b"resource-owned bytes\n")
with source.open("rb") as stream:
    owner=native.map(stream.fileno())
assert owner is not None
view=memoryview(owner)
del owner
gc.collect()
assert view.tobytes()==b"resource-owned bytes\n"
view.release()
source.unlink()
empty=Path("empty.bin")
empty.touch()
fd_root=Path("/proc/self/fd") if Path("/proc/self/fd").is_dir() else Path("/dev/fd")
# Stabilize imports and fd enumeration before measuring failed constructors.
before=len(list(fd_root.iterdir())) if fd_root.is_dir() else None
for _ in range(40):
    try:
        native.map(-1)
    except OSError:
        pass
    else:
        raise AssertionError("negative descriptor was accepted")
    with empty.open("rb") as stream:
        try:
            native.map(stream.fileno())
        except (OSError,ValueError):
            pass
        else:
            raise AssertionError("empty input was mapped")
gc.collect()
if before is not None:
    assert len(list(fd_root.iterdir()))==before
source.write_bytes(b"reentrant owner")
for operation in ("index", "slice", "find"):
    with source.open("rb") as stream:
        current=native.map(stream.fileno())
    class ClosingIndex:
        def __index__(self):
            current.close()
            return 0
    try:
        if operation=="index":
            current[ClosingIndex()]
        elif operation=="slice":
            current[slice(ClosingIndex(),None)]
        else:
            current.find(b"x",ClosingIndex(),3)
    except ValueError:
        pass
    else:
        raise AssertionError("index conversion used a closed mapping")
source.write_bytes(b"audited bytes")
events=[]
def audit(event,args):
    if event=="mmap.__new__":
        events.append(args)
        raise PermissionError("mapping audit refused")
sys_module=__import__("sys")
sys_module.addaudithook(audit)
with source.open("rb") as stream:
    descriptor=stream.fileno()
    try:
        native.map(descriptor)
    except PermissionError:
        pass
    else:
        raise AssertionError("mapping bypassed the audit refusal")
    assert stream.read()==b"audited bytes"
assert len(events)==1
assert events[0][0]==descriptor and events[0][2]==1
print("mapping ownership passed")
"""
    result = run_python(code, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mapping ownership passed" in result.stdout
