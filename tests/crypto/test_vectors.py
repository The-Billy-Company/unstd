"""Known-answer vectors for unstd.crypto, from the BLAKE3 authors' own fixture.

``blake3_reference_vectors.json`` beside this file is the reference test vector
set published by the BLAKE3 team, vendored verbatim. Nothing in here is computed
by the code under test, and that distinction is the whole point: a vector whose
answer comes from the implementation proves only that the implementation agrees
with itself, while these are the values a second implementation has to match.

Each case exercises all three BLAKE3 modes on the same input — plain ``hash``
(:func:`digest.hex`), ``keyed_hash`` (:func:`token.mac_hex`), and ``derive_key``
(:func:`digest.derive_key`) — which is what makes a mode mix-up detectable. The
reference outputs are extended (131-byte XOF) reads; a 32-byte digest is their
first 64 hex characters, because every prefix of a BLAKE3 output is itself a
digest.

The one value here that is *not* externally published is the claims-envelope
golden, because the envelope is this package's own format rather than BLAKE3's.
Its MAC is already proven against the reference above, so the golden pins only
the composition: which bytes get signed, and how they are spelled on the wire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unstd.crypto import digest, token


_REFERENCE_PATH = Path(__file__).resolve().parent / "blake3_reference_vectors.json"
_REFERENCE = json.loads(_REFERENCE_PATH.read_text())

_KEY: bytes = _REFERENCE["key"].encode("ascii")
_CONTEXT: str = _REFERENCE["context_string"]
_CASES: list[dict[str, object]] = _REFERENCE["cases"]

_DIGEST_HEX = digest.DIGEST_BYTES * 2


def _input(length: int) -> bytes:
    """The reference filler: byte ``i`` is ``i % 251``, 251 being the largest prime < 256."""
    return bytes(i % 251 for i in range(length))


def _lengths() -> list[int]:
    return [int(case["input_len"]) for case in _CASES]  # type: ignore[call-overload]


@pytest.fixture(params=_CASES, ids=lambda c: f"len{c['input_len']}")
def case(request: pytest.FixtureRequest) -> dict[str, object]:
    """One reference case, as the fixture every mode test reads."""
    return dict(request.param)


class TestTheFixtureIsTheRealOne:
    """A vendored fixture can be truncated or hand-edited; these notice."""

    def test_it_is_the_published_key_and_context(self):
        assert _REFERENCE["key"] == "whats the Elvish word for friend"
        assert _CONTEXT == "BLAKE3 2019-12-27 16:29:52 test vectors context"
        assert len(_KEY) == token.KEY_BYTES

    def test_it_still_spans_the_chunk_and_tree_boundaries(self):
        """BLAKE3's bugs live at 1024-byte chunk edges and subtree joins.

        A fixture trimmed to a handful of short inputs would still pass every
        other test in this file while testing none of the tree logic, so the
        boundary lengths are named rather than counted.
        """
        lengths = set(_lengths())
        assert {0, 1, 1023, 1024, 1025, 2048, 2049, 8192, 31744} <= lengths
        assert len(_CASES) >= 35

    def test_every_case_carries_all_three_modes(self):
        for c in _CASES:
            assert {"input_len", "hash", "keyed_hash", "derive_key"} <= set(c)


class TestReferenceVectors:
    def test_hash_matches_the_reference(self, case: dict[str, object]):
        data = _input(int(case["input_len"]))  # type: ignore[call-overload]
        expect = str(case["hash"])[:_DIGEST_HEX]
        assert digest.hex(data) == expect
        assert digest.raw(data) == bytes.fromhex(expect)

    def test_keyed_hash_matches_the_reference(self, case: dict[str, object]):
        """Keyed BLAKE3 is the MAC, so the reference's keyed answers gate the MAC."""
        data = _input(int(case["input_len"]))  # type: ignore[call-overload]
        expect = str(case["keyed_hash"])[:_DIGEST_HEX]
        assert token.mac_hex(_KEY, data) == expect
        assert token.mac_raw(_KEY, data) == bytes.fromhex(expect)
        assert token.verify_mac(_KEY, data, expect)

    def test_derive_key_matches_the_reference(self, case: dict[str, object]):
        data = _input(int(case["input_len"]))  # type: ignore[call-overload]
        expect = str(case["derive_key"])[:_DIGEST_HEX]
        assert digest.derive_key(_CONTEXT, data).hex() == expect
        assert len(digest.derive_key(_CONTEXT, data)) == digest.DIGEST_BYTES

    def test_the_three_modes_never_collide_on_one_input(self, case: dict[str, object]):
        """Same bytes, three modes, three unrelated answers — the flag domain separation.

        A binding that dropped the key or the context would land one mode on
        another's answer, and every single-mode assertion above would still pass
        for the mode that stayed correct.
        """
        answers = {
            str(case[mode])[:_DIGEST_HEX]
            for mode in ("hash", "keyed_hash", "derive_key")
        }
        assert len(answers) == 3


class TestPublishedConstants:
    def test_empty_digest_is_the_constant_every_implementation_publishes(self):
        # Spelled out rather than read from the fixture: if the vendored file
        # were ever replaced wholesale, this line still catches it.
        assert (
            digest.hex(b"")
            == "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"
        )

    def test_abc_digest_is_the_widely_published_constant(self):
        assert (
            digest.hex(b"abc")
            == "6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85"
        )


class TestTruncationAndStreaming:
    """Two claims about shape that the reference answers can referee."""

    @pytest.mark.parametrize("n", [1, 6, 8, 16, 32])
    def test_truncation_is_a_prefix_of_the_full_digest(self, n: int):
        expect = str(_CASES[3]["hash"])[:_DIGEST_HEX]
        data = _input(int(_CASES[3]["input_len"]))  # type: ignore[call-overload]
        assert digest.hex(data, n) == expect[: n * 2]

    @pytest.mark.parametrize(
        "chunks", [(1,), (1, 1, 1), (1023, 1), (1, 1023), (512, 512)]
    )
    def test_streaming_agrees_with_one_shot_across_a_chunk_boundary(
        self, chunks: tuple[int, ...]
    ):
        """Where an incremental hasher breaks is the 1024-byte chunk edge."""
        data = _input(sum(chunks))
        stream, at = digest.Stream(), 0
        for width in chunks:
            stream.update(data[at : at + width])
            at += width
        assert stream.hex() == digest.hex(data)
        assert digest.Stream([data[:1], data[1:]]).raw() == digest.raw(data)


class TestKdfDomainSeparation:
    """The property the context argument exists for, stated three ways."""

    def test_same_material_two_contexts_two_unrelated_keys(self):
        material = _input(64)
        a = digest.derive_key("unstd test context v1", material)
        b = digest.derive_key("unstd test context v2", material)
        assert a != b
        assert len(a) == len(b) == digest.DIGEST_BYTES

    def test_a_one_character_context_change_is_a_different_key(self):
        material = b"the same material either way"
        assert digest.derive_key("ctx", material) != digest.derive_key("ctx ", material)

    def test_derived_key_is_not_the_bare_digest_of_the_material(self):
        """A KDF that forgot its context would just be a digest of the material."""
        material = _input(32)
        assert digest.derive_key(_CONTEXT, material) != digest.raw(material)


class TestClaimsEnvelope:
    """The one format this package owns, so the one golden it captures itself."""

    KEY = bytes(range(1, 33))
    CLAIMS = b'{"sub":"u1","exp":1767225600,"aud":"example"}'
    GOLDEN = (
        "eyJzdWIiOiJ1MSIsImV4cCI6MTc2NzIyNTYwMCwiYXVkIjoiZXhhbXBsZSJ9"
        ".UM4aL4802CKbmyfa1vlnQ9Gs0A-zvS43YFLx7OTWViw"
    )

    def test_minting_reproduces_the_golden(self):
        assert token.mint(self.KEY, self.CLAIMS) == self.GOLDEN

    def test_the_golden_verifies_and_yields_its_claims(self):
        assert token.verify(self.KEY, self.GOLDEN) == self.CLAIMS

    def test_the_payload_is_unpadded_base64url_of_the_claims(self):
        """Readable on the wire, and padding-free — a ``=`` would break the format."""
        payload, _, tag = self.GOLDEN.partition(token.SEPARATOR)
        assert "=" not in payload
        assert "=" not in tag
        assert token.verify(self.KEY, self.GOLDEN) == self.CLAIMS

    def test_the_mac_covers_the_encoded_payload_not_the_raw_claims(self):
        """The detail a second implementation gets wrong silently.

        Both candidates produce a well-formed 32-byte tag, so nothing but this
        comparison distinguishes them until two services disagree in production.
        """
        payload, _, tag = self.GOLDEN.partition(token.SEPARATOR)
        over_payload = token.mac_raw(self.KEY, payload.encode("ascii"))
        over_raw = token.mac_raw(self.KEY, self.CLAIMS)
        assert over_payload != over_raw
        assert len(over_payload) == len(over_raw) == token.TAG_BYTES
        assert tag == token.mint(self.KEY, self.CLAIMS).partition(token.SEPARATOR)[2]
