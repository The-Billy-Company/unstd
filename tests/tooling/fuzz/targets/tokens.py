"""Bearer token v1: selector modulo three, then attacker-controlled bytes.

0 verifies arbitrary UTF-8/surrogate text; 1 derives an authentic opaque-claims
bearer with the fixed public test key, then changes claims, tag, separators and
unused pad bits; 2 signs deliberately malformed payload spellings independently
of unstd. Claims in generated modes are bounded to 8192 bytes. The independent
model uses raw keyed BLAKE3, stdlib Base64 and hmac comparison. It demands exactly
two nonempty canonical unpadded Base64url segments and a 32-byte signature over
the ASCII payload. No JSON, expiry, audience or other authorization policy is
invented; opaque claims bytes alone are authenticated. Verification of arbitrary
text has no permitted exceptions. The key and alternate key are test fixtures.
"""

from __future__ import annotations

import base64
import binascii
import hmac

from blake3 import blake3

from unstd.crypto import token

from .json import text


KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sign(payload: str, key: bytes = KEY) -> str:
    return (
        payload
        + "."
        + _encode(blake3(payload.encode("utf-8", "surrogatepass"), key=key).digest())
    )


def _model(candidate: str, key: bytes = KEY) -> bytes | None:
    parts = candidate.split(".")
    if len(parts) != 2:
        return None
    for segment in parts:
        if not segment or any(char not in _ALPHABET for char in segment):
            return None
    payload, signature = parts
    try:
        claims, tag = (
            base64.b64decode(
                part.encode("ascii") + b"=" * (-len(part) % 4),
                altchars=b"-_",
                validate=True,
            )
            for part in parts
        )
    except binascii.Error:
        return None
    if not claims or len(tag) != 32:
        return None
    if _encode(claims) != payload or _encode(tag) != signature:
        return None
    expected = blake3(payload.encode("ascii"), key=key).digest()
    return claims if hmac.compare_digest(tag, expected) else None


def _verify(candidate: str, key: bytes = KEY) -> None:
    # No catch: token text is attacker-controlled, so *any* exception is a bug.
    actual = token.verify(key, candidate)
    expected = _model(candidate, key)
    assert actual == expected
    if actual is not None:
        assert type(actual) is bytes
        assert actual
        assert candidate == _sign(_encode(actual), key)


def fuzz(data: bytes) -> None:
    """Exercise authentic controls, hostile text and independently signed shapes."""
    selector, raw = (data[0], data[1:]) if data else (0, b"")
    mode = selector % 3
    if mode == 0:
        _verify(text(raw))
        return
    claims = raw[:8192] or b"\x00"
    payload = _encode(claims)
    valid = _sign(payload)
    assert token.mint(KEY, claims) == valid
    assert (
        token.mac_raw(KEY, payload.encode())
        == blake3(payload.encode(), key=KEY).digest()
    )
    assert token.mac_hex(KEY, claims) == blake3(claims, key=KEY).hexdigest()
    _verify(valid)
    _verify(valid, OTHER_KEY)
    assert token.verify(KEY, valid) == claims
    if mode == 1:
        changed_claims = bytearray(claims)
        changed_claims[selector % len(claims)] ^= 1
        signature = valid.split(".")[1]
        changed_tag = bytearray(blake3(payload.encode(), key=KEY).digest())
        changed_tag[selector % 32] ^= 1
        candidates = [
            _encode(changed_claims) + "." + signature,
            payload + "." + _encode(changed_tag),
            valid.replace(".", ""),
            valid.replace(".", ".."),
            valid + ".",
            "." + valid,
            payload + "=." + signature,
            payload + "." + signature + "=",
            payload + "." + signature[:-1],
            payload + "." + signature + text(raw[:8]),
        ]
        # Every 32-byte signature has two unused low bits. Aliases decode to the
        # authentic MAC yet violate the canonical envelope's one-spelling rule.
        final = _ALPHABET.index(signature[-1])
        assert final % 4 == 0
        candidates.extend(
            payload + "." + signature[:-1] + _ALPHABET[final | low] for low in (1, 2, 3)
        )
        for candidate in candidates:
            _verify(candidate)
    else:
        malformed = (
            "",
            "Zg==",
            "Zg!",
            "Zh",  # unused payload bits: decodes to f, but f encodes to Zg
            "/w",
            "+w",
            payload + "\x00",
            payload + "\n",
            text(raw[:512]),
        )
        for spelling in malformed:
            _verify(_sign(spelling))
        for width in (0, 1, 16, 31, 33, 64):
            _verify(payload + "." + _encode(bytes(width)))
    # MAC comparison's arbitrary text contract also refuses non-ASCII/surrogates
    # without raising; accepted hex must equal the independently derived tag.
    candidate_tag = text(raw[:256])
    expected_hex = blake3(claims, key=KEY).hexdigest()
    expected_raw = blake3(claims, key=KEY).digest()
    assert token.verify_mac(KEY, claims, expected_hex) is True
    assert token.verify_mac(KEY, claims, expected_raw) is True
    assert token.verify_mac(OTHER_KEY, claims, expected_hex) is False
    changed_mac = bytes([expected_raw[0] ^ 1]) + expected_raw[1:]
    assert token.verify_mac(KEY, claims, changed_mac) is False
    assert token.verify_mac(KEY, claims, candidate_tag) is (
        candidate_tag == expected_hex
    )
