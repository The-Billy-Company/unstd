"""The struct dtype parser accepts only documented typestr spellings.

Original failure: the item size was read with ``int()``, so the portable helpers
and the stdlib fallback accepted ``"i4 "``, ``"i0_4"``, ``"i+4"`` and non-ASCII
digits such as ``"i٤"`` as ``int32`` — spellings NumPy rejects or that no
typestr grammar contains — making "portable" validation looser than any backend.
"""

from __future__ import annotations

import re
import struct

import pytest

from unstd.pack import binpack


_LOOSE = ["i4 ", " i4", "i 4", "i+4", "i04", "<i0_4", "i\u0664", "f\uff14", "u\t8"]


@pytest.mark.parametrize("dtype", _LOOSE)
def test_portable_helpers_reject_loose_size_spellings(dtype: str):
    message = rf"^unstd\.pack: unsupported array dtype {re.escape(repr(dtype))}$"
    with pytest.raises(binpack.error, match=message):
        binpack.pack_array_portable(dtype, [1])
    with pytest.raises(binpack.error, match=message):
        binpack.unpack_array_portable(dtype, bytes(8))
    assert binpack.unpack_array_portable("i4", struct.pack("=i", 9)) == [9]


@pytest.mark.parametrize("dtype", _LOOSE)
def test_stdlib_fallback_rejects_loose_size_spellings(
    dtype: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(binpack, "_HAVE_NUMPY", False)
    monkeypatch.setattr(binpack, "np", None)
    with pytest.raises(binpack.error, match="unsupported array dtype"):
        binpack.pack_array(dtype, [1])
    with pytest.raises(binpack.error, match="unsupported array dtype"):
        binpack.unpack_array(dtype, bytes(8))
    assert binpack.pack_array("i4", [1]) == struct.pack("=i", 1)
