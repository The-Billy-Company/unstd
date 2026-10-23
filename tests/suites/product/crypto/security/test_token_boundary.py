"""Only canonical independently authenticated envelopes grant their exact bytes.

The primitive is tested against published vectors elsewhere. Here stdlib Base64
and the raw BLAKE3 primitive construct independent expected envelopes; every
negative has an authentic successful control, not an incidental parse failure.
"""

from __future__ import annotations

import base64
import binascii

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.crypto import token


blake3 = pytest.importorskip("blake3").blake3
_KEY = bytes(range(32))
_OTHER_KEY = bytes(reversed(range(32)))
_CLAIMS = b'{"sub":"fixture","admin":false,"exp":0}'
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _envelope(claims: bytes, key: bytes = _KEY) -> str:
    payload = _encode(claims)
    signature = blake3(payload.encode("ascii"), key=key).digest()
    return payload + "." + _encode(signature)


def _sign_payload(payload: str) -> str:
    return payload + "." + _encode(blake3(payload.encode("ascii"), key=_KEY).digest())


@given(claims=st.binary(min_size=1, max_size=2048))
def test_independent_envelope_and_public_minter_agree_on_exact_opaque_bytes(claims):
    expected = _envelope(claims)
    assert token.mint(_KEY, claims) == expected
    assert token.verify(_KEY, expected) == claims
    assert token.verify(_OTHER_KEY, expected) is None


@pytest.mark.parametrize("segment", ["payload", "tag"])
@pytest.mark.parametrize(
    "junk", ["!", " ", "\t", "\r\n", "\x00", "=", "==", "é", "😀", "\ud800"]
)
def test_segment_junk_cannot_inherit_an_authentic_tag(segment, junk):
    good = _envelope(_CLAIMS)
    payload, tag = good.split(".")
    assert token.verify(_KEY, good) == _CLAIMS
    bad = (
        payload + junk + "." + tag
        if segment == "payload"
        else payload + "." + tag + junk
    )
    assert token.verify(_KEY, bad) is None


def test_unused_signature_bits_cannot_create_equivalent_authenticated_tokens():
    good = _envelope(_CLAIMS)
    payload, tag = good.split(".")
    index = _ALPHABET.index(tag[-1])
    assert index % 4 == 0  # A 32-byte tag leaves two unused bits.
    for low_bits in (1, 2, 3):
        alias = tag[:-1] + _ALPHABET[index | low_bits]
        assert base64.urlsafe_b64decode(alias + "=") == base64.urlsafe_b64decode(
            tag + "="
        )
        assert token.verify(_KEY, payload + "." + alias) is None
    assert token.verify(_KEY, good) == _CLAIMS


@pytest.mark.parametrize("payload", ["Zg==", "Zg!", "Zh", "/w", "+w", "!!!!"])
def test_even_a_valid_mac_cannot_bless_a_noncanonical_payload(payload):
    # The key owner signs these invalid spellings deliberately. The shape rule
    # must hold independently of successful MAC authentication.
    assert token.verify(_KEY, _envelope(b"f")) == b"f"
    assert token.verify(_KEY, _sign_payload(payload)) is None


@pytest.mark.parametrize("segment", [0, 1])
def test_every_byte_truncation_and_separator_mutation_is_refused(segment):
    good = _envelope(_CLAIMS)
    parts = good.split(".")
    for end in range(len(parts[segment])):
        changed = parts.copy()
        changed[segment] = changed[segment][:end]
        assert token.verify(_KEY, ".".join(changed)) is None
    for malformed in [
        good.replace(".", ""),
        "." + good,
        good + ".",
        good.replace(".", ".."),
    ]:
        assert token.verify(_KEY, malformed) is None
    assert token.verify(_KEY, good) == _CLAIMS


def test_every_claim_and_signature_byte_flip_is_rejected_with_authentic_control():
    good = _envelope(_CLAIMS)
    payload, tag = good.split(".")
    signature = base64.urlsafe_b64decode(tag + "=")
    for offset in range(len(_CLAIMS)):
        changed = bytearray(_CLAIMS)
        changed[offset] ^= 1
        assert token.verify(_KEY, _encode(changed) + "." + tag) is None
    for offset in range(len(signature)):
        changed = bytearray(signature)
        changed[offset] ^= 1
        assert token.verify(_KEY, payload + "." + _encode(changed)) is None
    assert token.verify(_KEY, good) == _CLAIMS


@given(candidate=st.text(max_size=512))
def test_untrusted_unicode_tokens_never_raise_and_accept_only_the_exact_envelope(
    candidate,
):
    # Successful verification must satisfy a separate primitive+canonical model.
    actual = token.verify(_KEY, candidate)
    if actual is not None:
        assert actual
        assert candidate == _envelope(actual)


@pytest.mark.parametrize("candidate", ["é", "😀", "\ud800", "\x00é", "a" * 64 + "é"])
def test_public_mac_comparison_refuses_unicode_without_a_server_error(candidate):
    assert token.equal(candidate, candidate) is False
    assert token.verify_mac(_KEY, _CLAIMS, candidate) is False
    correct = blake3(_CLAIMS, key=_KEY).hexdigest()
    assert token.verify_mac(_KEY, _CLAIMS, correct)


@pytest.mark.parametrize("width", [0, 1, 16, 31, 33, 64])
def test_invalid_signer_key_is_a_configuration_error_not_a_false_authentication(width):
    good = _envelope(_CLAIMS)
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        token.verify(bytes(width), good)
    assert token.verify(_KEY, good) == _CLAIMS


def test_opaque_claims_are_authenticated_without_inventing_authorization_policy():
    for claims in [b"not JSON", b"\x00\xff", b'{"exp":0,"aud":"other"}']:
        assert token.verify(_KEY, _envelope(claims)) == claims


def test_url_codec_remains_lenient_while_the_bearer_boundary_is_strict():
    from unstd.serde import b64

    good = _envelope(_CLAIMS)
    payload, tag = good.split(".")
    assert b64.decode(tag + "=!", url=True) == base64.urlsafe_b64decode(tag + "=")
    assert token.verify(_KEY, payload + "." + tag + "!") is None
    with pytest.raises(binascii.Error):
        b64.decode(tag + "!", url=False)


def test_default_bound_covers_the_complete_envelope_and_is_inclusive():
    claims = b"x" * 6111
    at_limit = _envelope(claims)
    over_limit = _envelope(claims + b"x")
    assert len(at_limit) == 8192
    assert len(over_limit) > 8192
    assert token.verify(_KEY, at_limit) == claims
    assert token.verify(_KEY, over_limit) is None
    assert (
        token.verify(_KEY, over_limit, max_token_chars=len(over_limit)) == claims + b"x"
    )
    assert token.verify(_KEY, over_limit, max_token_chars=None) == claims + b"x"
    assert token.verify(_KEY, at_limit, max_token_chars=len(at_limit) - 1) is None


@pytest.mark.parametrize(
    "candidate",
    ["x" * 8193, "😀" * 8193, "\ud800" * 8193],
    ids=["ascii", "astral", "lone-surrogate"],
)
def test_oversized_text_is_rejected_before_slicing_scanning_decoding_or_hashing(
    monkeypatch, candidate
):
    class UnreadableText(str):
        def __len__(self):
            return 1

        def partition(self, _separator):
            pytest.fail("oversized token contents must not be inspected")

    def forbidden(*_args, **_kwargs):
        pytest.fail("oversized tokens must not reach codecs or the MAC")

    monkeypatch.setattr(token.b64, "decode", forbidden)
    monkeypatch.setattr(token, "mac_raw", forbidden)
    # An invalid key must not turn an oversized request into a server error.
    unreadable = UnreadableText(candidate)
    assert len(unreadable) == 1  # A subclass hook cannot bypass the actual bound.
    assert token.verify(b"", unreadable) is None


@pytest.mark.parametrize("candidate", ["", "x", _envelope(_CLAIMS)])
@pytest.mark.parametrize("limit", [True, False, 1.5, 8192.0, "8192", b"8192", object()])
def test_noninteger_limit_is_a_configuration_error(limit, candidate):
    with pytest.raises(TypeError, match=r"max_token_chars.*positive integer or None"):
        token.verify(_KEY, candidate, max_token_chars=limit)


@pytest.mark.parametrize("candidate", ["", "x", _envelope(_CLAIMS)])
@pytest.mark.parametrize(
    "limit", [0, -1, -8192, pytest.param(-(1 << 10000), id="arbitrary-precision")]
)
def test_nonpositive_limit_is_a_configuration_error(limit, candidate):
    with pytest.raises(ValueError, match=r"max_token_chars.*positive integer or None"):
        token.verify(_KEY, candidate, max_token_chars=limit)


def test_arbitrary_precision_positive_limit_preserves_authentication_and_key_errors():
    limit = 1 << 10000
    bearer = _envelope(_CLAIMS)
    assert token.verify(_KEY, bearer, max_token_chars=limit) == _CLAIMS
    assert token.verify(_OTHER_KEY, bearer, max_token_chars=limit) is None
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        token.verify(b"", bearer, max_token_chars=limit)


@pytest.mark.parametrize("candidate", ["", "x", _envelope(_CLAIMS)])
def test_equal_integer_subclass_cannot_bypass_limit_configuration_checks(candidate):
    class IntegerLimit(int):
        pass

    with pytest.raises(TypeError, match=r"max_token_chars.*positive integer or None"):
        token.verify(_KEY, candidate, max_token_chars=IntegerLimit(8192))


@pytest.mark.parametrize("width", [1, 2, 3, 4, 6, 7, 8])
def test_all_terminal_base64_bits_match_independent_canonicality_and_key_errors(width):
    for final in _ALPHABET:
        payload = "A" * (width - 1) + final
        bearer = _sign_payload(payload)
        try:
            claims = base64.b64decode(
                payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
            )
        except binascii.Error:
            claims = None
        canonical = claims is not None and _encode(claims) == payload
        assert token.verify(_KEY, bearer) == (claims if canonical else None)
        if canonical:
            with pytest.raises(ValueError, match="exactly 32 bytes"):
                token.verify(b"", bearer)
        else:
            assert token.verify(b"", bearer) is None


@pytest.mark.parametrize(
    "payload", ["", "a", "Zh", "Zg=", "Zg!", "+w", "/w", "é", "😀", "\ud800"]
)
def test_every_malformed_payload_precedes_signer_key_validation(payload):
    # A fixed-width canonical tag keeps the failure at the payload boundary.
    signature = _envelope(_CLAIMS).split(".")[1]
    assert token.verify(b"", payload + "." + signature) is None


def test_plain_string_and_key_subclasses_retain_exact_bytes_and_error_order():
    class TokenText(str):
        def __len__(self):
            return 0

        def __getitem__(self, _index):
            pytest.fail("string subclass slicing hooks must not replace partition")

    class SignerKey(bytes):
        pass

    bearer = TokenText(_envelope(_CLAIMS))
    assert token.verify(SignerKey(_KEY), bearer) == _CLAIMS
    assert token.verify(SignerKey(_KEY), bearer, max_token_chars=None) == _CLAIMS
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        token.verify(SignerKey(b""), bearer)
    for malformed in [
        TokenText("a." + bearer.split(".")[1]),
        TokenText("é." + "A" * 43),
    ]:
        assert token.verify(SignerKey(b""), malformed) is None


def test_explicit_unbounded_verification_preserves_subclass_partition_hooks():
    good = _envelope(_CLAIMS)

    class PartitionToken(str):
        def partition(self, separator):
            return good.partition(separator)

    wrapped = PartitionToken("wrapper")
    assert token.verify(_KEY, wrapped) is None
    assert token.verify(_KEY, wrapped, max_token_chars=None) == _CLAIMS


def test_forged_canonical_payload_is_not_decoded_before_authentication(monkeypatch):
    bearer = _envelope(_CLAIMS)
    payload, signature = bearer.split(".")
    decode = token.b64.decode
    decoded = []

    def observe(value, **kwargs):
        decoded.append(value)
        return decode(value, **kwargs)

    monkeypatch.setattr(token.b64, "decode", observe)
    assert token.verify(_OTHER_KEY, bearer) is None
    assert decoded == [signature]
    decoded.clear()
    assert token.verify(_KEY, bearer) == _CLAIMS
    assert decoded == [signature, payload.encode("ascii")]
