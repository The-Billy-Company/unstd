"""Native offset copies and portable reads share values, errors, and ownership."""

from __future__ import annotations

import json

import pytest

from tests.support.environment.process import run_python
from unstd.dataset import Dataset, build
import unstd.dataset.storage as storage


@pytest.mark.parametrize("backend", ["python", "native"])
def test_backend_independently_matches_public_sequence_contract(
    tmp_path, monkeypatch, backend
):
    native = (
        None
        if backend == "python"
        else pytest.importorskip("unstd.dataset._dataset_native")
    )
    monkeypatch.setattr(storage, "_native", native)
    rows = [
        {"id": index, "text": "東京\n café", "values": [index, None]}
        for index in range(128)
    ]
    path = tmp_path / "rows.dataset"
    build(path, rows)
    with Dataset(path) as dataset:
        assert dataset[0] == rows[0]
        assert dataset[-1] == rows[-1]
        assert dataset.take([127, 0, 35, 35, -3]) == [
            rows[index] for index in [127, 0, 35, 35, -3]
        ]
        assert dataset[7:121:9] == rows[7:121:9]
        assert dataset[::-3] == rows[::-3]
        assert list(dataset.iter_rows(-5, None)) == rows[-5:]
        assert list(dataset.batches(7, start=-19, stop=-2)) == [
            rows[index : min(index + 7, 126)] for index in range(109, 126, 7)
        ]
        assert dataset.verify() is None


@pytest.mark.parametrize("backend", ["python", "native"])
def test_corrupt_offset_rejection_is_shared_by_backends(tmp_path, monkeypatch, backend):
    import struct

    native = (
        None
        if backend == "python"
        else pytest.importorskip("unstd.dataset._dataset_native")
    )
    monkeypatch.setattr(storage, "_native", native)
    path = tmp_path / "rows.dataset"
    build(path, [0, 1, 2])
    raw = bytearray(path.read_bytes())
    _, count, index_offset, _ = struct.unpack_from("<8sQQ32s", raw)
    assert count == 3
    struct.pack_into("<Q", raw, index_offset + 8, 2**64 - 1)
    path.write_bytes(raw)
    with Dataset(path) as dataset:
        assert dataset[2] == 2
        for operation in (
            lambda: dataset[0],
            lambda: dataset.take([1]),
            lambda: list(dataset.batches()),
            dataset.verify,
        ):
            with pytest.raises(ValueError, match="dataset"):
                operation()


def test_hostile_native_geometry_and_exception_cleanup_in_bounded_worker(tmp_path):
    pytest.importorskip("unstd.dataset._dataset_native")
    code = r"""
import json
import struct
from hashlib import sha256
from unstd.dataset import _dataset_native as native
header=struct.Struct("<8sQQ32s")
magic=b"UNSTDDS\x02"
payloads=[b"null\n", b'{"x":1}\n', b'"text"\n']
offsets=[header.size]
for payload in payloads:
    offsets.append(offsets[-1]+len(payload))
index_offset=offsets[-1]
body=b"".join(payloads)+struct.pack("<4Q",*offsets)
raw=header.pack(magic,3,index_offset,sha256(body).digest())+body
checks=0

def reject(fn, kinds):
    global checks
    try:
        fn()
    except kinds:
        checks+=1
    else:
        raise AssertionError("malformed native input was accepted")

def release_check(data, fn, kinds):
    reject(fn,kinds)
    data.extend(b"release-check")

assert native.KERNEL_VERSION == 2
assert native.row(raw,index_offset,3,1)==b'{"x":1}'
assert native.take(raw,index_offset,3,(2,0,2))==[b'"text"',b'null',b'"text"']
assert native.span(raw,index_offset,3,0,2)==b"".join(payloads[:2])
assert native.span(raw,index_offset,3,0,0)==b""
assert native.span(raw,index_offset,3,3,3)==b""
assert native.validate(raw,index_offset,3) is None
for value in [b"",b"x"*55,raw[:-1],raw+b"x"]:
    data=bytearray(value)
    release_check(data,lambda:native.row(data,index_offset,3,0),(ValueError,))
for bad_index,bad_count in [(-1,3),(0,3),(index_offset+1,3),(index_offset,-1),(index_offset,2)]:
    data=bytearray(raw)
    release_check(data,lambda:native.validate(data,bad_index,bad_count),(ValueError,))
for value in [-1,3]:
    data=bytearray(raw)
    release_check(data,lambda:native.row(data,index_offset,3,value),(IndexError,))
for value in [2**100,-(2**100)]:
    data=bytearray(raw)
    release_check(data,lambda:native.row(data,index_offset,3,value),(OverflowError,))
for positions,kinds in [([0],(TypeError,)),((0,"1"),(TypeError,)),((0,1.5),(TypeError,)),((0,-1),(IndexError,)),((0,3),(IndexError,)),((0,2**100),(OverflowError,))]:
    data=bytearray(raw)
    release_check(data,lambda:native.take(data,index_offset,3,positions),kinds)
for first,last in [(-1,1),(2,1),(0,4)]:
    data=bytearray(raw)
    release_check(data,lambda:native.span(data,index_offset,3,first,last),(IndexError,))
for value in [0,header.size,2**64-1]:
    data=bytearray(raw)
    struct.pack_into("<Q",data,index_offset+8,value)
    release_check(data,lambda:native.row(data,index_offset,3,0),(ValueError,))
for operation in [lambda data:native.take(data,index_offset,3,(0,1)),lambda data:native.span(data,index_offset,3,0,3),lambda data:native.validate(data,index_offset,3)]:
    data=bytearray(raw)
    data[offsets[1]-1]=ord("x")
    release_check(data,lambda:operation(data),(ValueError,))
for byte_index in [0,7,8,16]:
    data=bytearray(raw)
    data[byte_index]^=1
    release_check(data,lambda:native.validate(data,index_offset,3),(ValueError,))
data=bytearray(raw)
with memoryview(data)[::2] as view:
    reject(lambda:native.row(view,index_offset,3,0),(BufferError,))
data.extend(b"released")
print(json.dumps({"checks":checks,"buffer_cleanup":True}))
"""
    result = run_python(code, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["checks"] >= 30
    assert report["buffer_cleanup"] is True


def test_no_native_binary_is_required_for_reading(tmp_path):
    path = tmp_path / "rows.dataset"
    build(path, [{"ok": True}, [1, 2], None])
    code = f"""
import importlib.abc
import sys
class MissingNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "unstd.dataset._dataset_native":
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0,MissingNative())
from unstd.dataset import Dataset
import unstd.dataset.storage as storage
assert storage._native is None
with Dataset({str(path)!r}) as rows:
    assert rows.take([2,0]) == [None,{{"ok":True}}]
    assert list(rows.batches(2)) == [[{{"ok":True}},[1,2]],[None]]
    assert rows.verify() is None
"""
    result = run_python(code, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("backend", ["python", "native"])
def test_internal_newlines_cannot_cancel_out_batch_record_counts(
    tmp_path, monkeypatch, backend
):
    from hashlib import sha256
    import struct

    native = (
        None
        if backend == "python"
        else pytest.importorskip("unstd.dataset._dataset_native")
    )
    monkeypatch.setattr(storage, "_native", native)
    header = struct.Struct("<8sQQ32s")
    payloads = [b"0\n1\n", b" \n"]
    offsets = [
        header.size,
        header.size + len(payloads[0]),
        header.size + sum(map(len, payloads)),
    ]
    body = b"".join(payloads) + struct.pack("<3Q", *offsets)
    path = tmp_path / "cancelled-framing.dataset"
    path.write_bytes(
        header.pack(b"UNSTDDS\x02", 2, offsets[-1], sha256(body).digest()) + body
    )
    with Dataset(path) as dataset:
        for operation in (
            lambda: dataset[0],
            lambda: dataset.take([0, 1]),
            lambda: list(dataset.batches(2)),
            dataset.verify,
        ):
            with pytest.raises(ValueError, match="dataset"):
                operation()


def test_unknown_kernel_version_uses_portable_reads(tmp_path):
    code = """
import sys
from types import ModuleType
kernel = ModuleType("unstd.dataset._dataset_native")
kernel.KERNEL_VERSION = 999
def unsupported(*args, **kwargs):
    raise AssertionError("an unsupported native kernel was used")
for name in ("row", "take", "span", "validate"):
    setattr(kernel, name, unsupported)
sys.modules[kernel.__name__] = kernel
from unstd.dataset import Dataset, build
import unstd.dataset.storage as storage
assert storage._native is None
build("rows.dataset", [0, {"id": 1}, None])
with Dataset("rows.dataset") as rows:
    assert rows.take([2, 0]) == [None, 0]
    assert list(rows.batches(2)) == [[0, {"id": 1}], [None]]
    assert rows.verify() is None
"""
    result = run_python(code, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
