"""A missing KDF context must not silently become plain BLAKE3 hashing.

Original failure: ``digest.derive_key(None, material)`` returned
``digest.raw(material)``, because the backend reads ``derive_key_context=None``
as "no mode". Every derived key then equalled the public digest of its material.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.backends import require_backend


digest = require_backend("unstd.crypto.digest")

_REFERENCE = json.loads(
    (Path(__file__).parents[1] / "unit" / "blake3_reference_vectors.json").read_text()
)
_CASE = next(case for case in _REFERENCE["cases"] if case["input_len"] == 1025)


@pytest.mark.parametrize("length", [0, 1025, 2 * 1024 * 1024])
def test_none_context_is_rejected_on_serial_and_parallel_paths(length: int):
    with pytest.raises(
        TypeError, match=r"^'NoneType' object is not an instance of 'str'$"
    ):
        digest.derive_key(None, bytes(length))  # type: ignore[arg-type]


def test_rejection_leaves_the_published_derivation_intact():
    material = bytes(i % 251 for i in range(1025))
    with pytest.raises(TypeError):
        digest.derive_key(None, material)  # type: ignore[arg-type]
    derived = digest.derive_key(_REFERENCE["context_string"], material)
    assert derived.hex() == _CASE["derive_key"][:64]
    assert derived != bytes.fromhex(_CASE["hash"][:64])
