"""Generated stream segmentation and hasher state laws, anchored to published vectors.

Every generated message is a prefix of the BLAKE3 reference filler, so any state
whose length is a published vector length is checked against the authors' answer.
Other lengths use the documented law ``Stream(parts).raw() == raw(b"".join(parts))``.
Both the Python update loop and the optional native batching helper are driven.
"""

from __future__ import annotations

from array import array
import importlib
import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)
import pytest

from tests.support.environment.backends import require_backend


digest = require_backend("unstd.crypto.digest")
try:
    _NATIVE = importlib.import_module("unstd.crypto._stream_native").absorb
except ImportError:
    _NATIVE = None

_REFERENCE = json.loads(
    (Path(__file__).parents[1] / "unit" / "blake3_reference_vectors.json").read_text()
)
_HASH = {case["input_len"]: case["hash"][:64] for case in _REFERENCE["cases"]}
_LENGTHS = sorted(_HASH)
_FILLER = bytes(i % 251 for i in range(_LENGTHS[-1]))
# BLAKE3 block (64) and chunk (1024) edges, plus the native helper's 2048-byte
# small-chunk limit; segment widths cluster here.
_EDGES = (0, 1, 63, 64, 65, 1023, 1024, 1025, 2047, 2048, 2049, 4096)
_KINDS = ("bytes", "bytearray", "memoryview", "readonly", "array")

_IMPLEMENTATIONS = [
    pytest.param(None, id="python"),
    pytest.param(
        _NATIVE,
        id="native",
        marks=pytest.mark.skipif(_NATIVE is None, reason="native helper not built"),
    ),
]


def _as(kind: str, chunk: bytes) -> object:
    match kind:
        case "bytearray":
            return bytearray(chunk)
        case "memoryview":
            return memoryview(chunk)
        case "readonly":
            return memoryview(bytearray(chunk)).toreadonly()
        case "array":
            return array("B", chunk)
    return chunk


def _construct(absorb: object, parts: list[object]) -> object:
    saved = digest._native_absorb
    digest._native_absorb = absorb
    try:
        return digest.Stream(parts)
    finally:
        digest._native_absorb = saved


@st.composite
def _segmented_vector(draw: st.DrawFn) -> tuple[int, list[object]]:
    """A published input cut anywhere — repeated cuts make empty parts."""
    length = draw(st.sampled_from(_LENGTHS))
    edges = [edge for edge in _EDGES if edge <= length]
    cut = st.one_of(
        st.integers(0, length),
        st.sampled_from(edges),
        st.sampled_from(edges).map(lambda edge: length - edge),
    )
    cuts = sorted(draw(st.lists(cut, max_size=20)))
    bounds = [0, *cuts, length]
    kinds = draw(st.lists(st.sampled_from(_KINDS), min_size=len(bounds) - 1))
    parts = [
        _as(kind, _FILLER[start:end])
        for kind, start, end in zip(kinds, bounds, bounds[1:], strict=False)
    ]
    # Exact lists and tuples of at least eight parts take the native path.
    return length, parts + [b""] * draw(st.integers(0, 8))


@pytest.mark.parametrize("absorb", _IMPLEMENTATIONS)
@given(case=_segmented_vector(), container=st.sampled_from((list, tuple, iter)))
def test_any_segmentation_of_a_published_input_gives_its_vector(
    absorb: object, case: tuple[int, list[object]], container: type
):
    length, parts = case
    expected = _HASH[length]
    stream = _construct(absorb, container(parts))
    assert stream.hex() == expected
    assert stream.raw() == bytes.fromhex(expected)

    chained = digest.Stream()
    for part in parts:
        assert chained.update(part) is chained
    assert chained.hex() == expected


@pytest.mark.parametrize("absorb", _IMPLEMENTATIONS)
@given(
    parts=st.lists(
        st.one_of(st.just(b""), st.binary(max_size=2100), st.binary(max_size=8)),
        max_size=40,
    )
)
def test_stream_of_parts_is_the_one_shot_digest_of_their_join(
    absorb: object, parts: list[bytes]
):
    joined = b"".join(parts)
    stream = _construct(absorb, parts)
    assert stream.raw() == digest.raw(joined)
    assert stream.hex(13) == digest.hex(joined, 13)


class _StreamMachine(RuleBasedStateMachine):
    """Update/read/rebuild sequences against the absorbed prefix of the filler."""

    absorb: object = None

    def __init__(self) -> None:
        super().__init__()
        self.saved = digest._native_absorb
        digest._native_absorb = self.absorb
        self.stream = digest.Stream()
        self.parts: list[bytes] = []
        self.length = 0

    def teardown(self) -> None:
        digest._native_absorb = self.saved

    def _absorb(self, width: int, kind: str) -> None:
        chunk = _FILLER[self.length : self.length + width]
        assert self.stream.update(_as(kind, chunk)) is self.stream
        self.parts.append(chunk)
        self.length += len(chunk)

    @initialize(width=st.sampled_from(_EDGES), kind=st.sampled_from(_KINDS))
    def start(self, width: int, kind: str) -> None:
        self._absorb(width, kind)

    @rule(
        width=st.one_of(st.sampled_from(_EDGES), st.integers(0, 3000)),
        kind=st.sampled_from(_KINDS),
    )
    def update(self, width: int, kind: str) -> None:
        self._absorb(width, kind)

    @precondition(lambda self: self.length < _LENGTHS[-1])
    @rule(kind=st.sampled_from(_KINDS))
    def advance_to_next_published_length(self, kind: str) -> None:
        target = next(n for n in _LENGTHS if n > self.length)
        self._absorb(target - self.length, kind)
        assert self.stream.hex() == _HASH[target]

    @rule(n=st.integers(1, digest.DIGEST_BYTES))
    def read_truncated(self, n: int) -> None:
        assert self.stream.hex(n) == digest.hex(_FILLER[: self.length], n)

    @rule(padding=st.integers(0, 8), container=st.sampled_from((list, tuple)))
    def rebuild_from_recorded_parts(self, padding: int, container: type) -> None:
        self.stream = digest.Stream(container(self.parts + [b""] * padding))

    @invariant()
    def digest_is_the_absorbed_prefix(self) -> None:
        expected = _HASH.get(self.length)
        prefix = _FILLER[: self.length]
        actual = self.stream.raw()
        if expected is not None:
            assert actual.hex() == expected
        assert actual == digest.raw(prefix)
        assert self.stream.raw() == actual


class _PythonStream(_StreamMachine):
    absorb = None


class _NativeStream(_StreamMachine):
    absorb = _NATIVE


TestPythonStreamStates = _PythonStream.TestCase
TestNativeStreamStates = pytest.mark.skipif(
    _NATIVE is None, reason="native helper not built"
)(_NativeStream.TestCase)
