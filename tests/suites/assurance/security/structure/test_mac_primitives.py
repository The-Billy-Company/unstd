"""Authentication must execute and honor the constant-time stdlib primitive.

Runtime observations catch dead calls and ignored results that source-string
checks cannot. These canaries make no claim to measure cryptographic timing.
"""

from __future__ import annotations

import base64
import hmac
import importlib
from types import SimpleNamespace

import pytest

from tests.support.backends import require_backend


_KEY = bytes(range(32))
_CLAIMS = b"authenticated fixture bytes"


@pytest.fixture
def token():
    require_backend("blake3")
    return importlib.import_module("unstd.crypto.token")


@pytest.fixture
def comparisons(token, monkeypatch):
    calls = []

    def compare(left, right):
        calls.append((left, right))
        return hmac.compare_digest(left, right)

    # Patch only this module's provider; pytest and other consumers keep hmac.
    monkeypatch.setattr(token, "hmac", SimpleNamespace(compare_digest=compare))
    return calls


@pytest.mark.parametrize("text", [False, True], ids=["bytes", "ascii"])
@pytest.mark.parametrize("authentic", [False, True], ids=["forged", "authentic"])
def test_equal_compares_complete_operands(token, comparisons, text, authentic):
    expected = b"complete tag"
    supplied = expected if authentic else b"complete tah"
    if text:
        expected, supplied = expected.decode("ascii"), supplied.decode("ascii")
    assert token.equal(expected, supplied) is authentic
    assert comparisons == [(expected, supplied)]


@pytest.mark.parametrize("api", ["raw", "hex", "bearer"])
@pytest.mark.parametrize("authentic", [False, True], ids=["forged", "authentic"])
def test_authentication_reaches_the_primitive_with_the_full_tag(
    token, comparisons, api, authentic
):
    # Construct tags with the raw backend and stdlib encoding, independently of
    # unstd's signer, codecs and comparison wrappers.
    blake3 = require_backend("blake3").blake3
    payload = base64.urlsafe_b64encode(_CLAIMS).decode("ascii").rstrip("=")
    message = payload.encode("ascii") if api == "bearer" else _CLAIMS
    expected = blake3(message, key=_KEY).digest()
    supplied = expected if authentic else expected[:-1] + bytes([expected[-1] ^ 1])
    if api == "bearer":
        signature = base64.urlsafe_b64encode(supplied).decode("ascii").rstrip("=")
        assert token.verify(_KEY, payload + "." + signature) == (
            _CLAIMS if authentic else None
        )
    else:
        if api == "hex":
            expected, supplied = expected.hex(), supplied.hex()
        assert token.verify_mac(_KEY, _CLAIMS, supplied) is authentic
    assert comparisons in ([(expected, supplied)], [(supplied, expected)])


@pytest.mark.parametrize("api", ["equal-bytes", "equal-text", "raw", "hex", "bearer"])
def test_a_primitive_refusal_cannot_be_ignored(token, monkeypatch, api):
    raw = token.mac_raw(_KEY, _CLAIMS)
    bearer = token.mint(_KEY, _CLAIMS)
    calls = []

    def refuse(left, right):
        calls.append((left, right))
        return False

    monkeypatch.setattr(token, "hmac", SimpleNamespace(compare_digest=refuse))
    if api.startswith("equal"):
        tag = raw if api == "equal-bytes" else raw.hex()
        assert token.equal(tag, tag) is False
    elif api == "bearer":
        assert token.verify(_KEY, bearer) is None
    else:
        assert (
            token.verify_mac(_KEY, _CLAIMS, raw if api == "raw" else raw.hex()) is False
        )
    assert len(calls) == 1
