"""The struct unpack path sizes every buffer exporter by bytes, not by ``len()``.

Original failure: ``unpack_array_portable("<i4", array("i", [1, 2]))`` raised
"buffer size 2 is not a multiple of itemsize 4" for an 8-byte buffer, a 1-D
``int32`` ndarray raised "unpack requires a buffer of 4 bytes", and a contiguous
3x4 ndarray reported "buffer size 3". Only memoryviews were measured in bytes.
"""

from __future__ import annotations

from array import array
import struct

import pytest

from tests.support.backends import require_backend
from unstd.pack import binpack


@pytest.fixture(params=["portable", "fallback"])
def unpack(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if request.param == "portable":
        return binpack.unpack_array_portable
    monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
    monkeypatch.setattr(binpack, "np", None)
    return binpack.unpack_array


@pytest.mark.parametrize(
    ("exporter", "values"),
    [
        (array("i", [1, -2]), [1, -2]),
        (array("h", [1, -2]), [struct.unpack("=i", struct.pack("=2h", 1, -2))[0]]),
        (array("d", [0.5]), list(struct.unpack("=2i", struct.pack("=d", 0.5)))),
    ],
    ids=["array-i", "array-h", "array-d"],
)
def test_array_exporters_are_measured_in_bytes(unpack, exporter, values):
    assert unpack("=i4", exporter) == values


def test_array_partial_elements_report_their_byte_size(unpack):
    with pytest.raises(
        binpack.error,
        match=r"^unstd\.pack: buffer size 6 is not a multiple of itemsize 4$",
    ):
        unpack("=i4", array("h", [1, 2, 3]))


def test_ndarray_exporters_are_measured_in_bytes(unpack):
    np = require_backend("numpy")
    assert unpack("<i4", np.arange(4, dtype="<i4")) == [0, 1, 2, 3]
    assert unpack("<i4", np.arange(12, dtype="<i4").reshape(3, 4)) == list(range(12))
    assert unpack("<u2", np.arange(3, dtype="<i4")) == [0, 0, 1, 0, 2, 0]
