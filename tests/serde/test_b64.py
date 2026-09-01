"""Adversarial + parity tests for ``unstd.serde.b64``.

Two contracts are pinned here:

1. The pybase64 backend and the stdlib ``base64`` fallback are **byte-for-byte**
   identical on encode, and agree on decode — including the specific regression
   this file exists to pin: pybase64 ``>=1.5`` flipped ``urlsafe_b64decode``'s
   own default to ``padded=False``, which would otherwise silently accept
   truncated/malformed urlsafe input the stdlib fallback rejects.
2. ``b64d``/``b64u_d`` reject malformed input loudly (``binascii.Error``), never
   silently truncate.
"""

from __future__ import annotations

import base64

import pytest

from unstd.serde import b64


try:
    import pybase64  # noqa: F401

    _HAVE_PYBASE64 = True
except ImportError:  # base install (this env) — pybase64-only assertions are skipped
    _HAVE_PYBASE64 = False

requires_pybase64 = pytest.mark.skipif(
    not _HAVE_PYBASE64, reason="pybase64 (`serde` extra) not installed"
)


# ── standard alphabet: byte-identical to stdlib ``base64`` ─────────────────────


@pytest.mark.parametrize(
    "data", [b"", b"h", b"he", b"hel", b"hell", b"hello world", bytes(range(256))]
)
def test_b64s_matches_stdlib(data: bytes) -> None:
    assert b64.b64s(data) == base64.b64encode(data).decode("ascii")


@pytest.mark.parametrize(
    "data", [b"", b"h", b"he", b"hel", b"hell", b"hello world", bytes(range(256))]
)
def test_b64d_round_trips_and_matches_stdlib(data: bytes) -> None:
    s = b64.b64s(data)
    assert b64.b64d(s) == data == base64.b64decode(s, validate=True)


def test_b64d_rejects_non_alphabet_bytes() -> None:
    with pytest.raises(b64.Error):
        b64.b64d("not valid base64!!")


def test_b64text_round_trips_utf8() -> None:
    text = "héllo wörld 🎉"
    assert b64.b64text(b64.b64s(text.encode())) == text


# ── url-safe alphabet: byte-identical to stdlib, including padding strictness ──


@pytest.mark.parametrize(
    "data", [b"", b"h", b"he", b"hel", b"hell", b"\xfb\xff\xfe", bytes(range(256))]
)
def test_b64u_s_matches_stdlib(data: bytes) -> None:
    assert b64.b64u_s(data) == base64.urlsafe_b64encode(data).decode("ascii")


@pytest.mark.parametrize(
    "data", [b"", b"h", b"he", b"hel", b"hell", b"\xfb\xff\xfe", bytes(range(256))]
)
def test_b64u_d_round_trips_and_matches_stdlib(data: bytes) -> None:
    s = b64.b64u_s(data)
    assert b64.b64u_d(s) == data == base64.urlsafe_b64decode(s)


def test_b64u_d_requires_canonical_padding_like_stdlib() -> None:
    """Regression: pybase64 >=1.5 defaults ``urlsafe_b64decode`` to ``padded=False``.

    ``"aGVsbG8"`` is ``b"hello"`` with its trailing ``=`` stripped — stdlib's
    ``urlsafe_b64decode`` raises on it, so ``b64u_d`` must too, on every backend.
    """
    unpadded = "aGVsbG8"
    with pytest.raises(Exception, match="padding"):
        base64.urlsafe_b64decode(unpadded)  # confirms the stdlib authority's behavior
    with pytest.raises(Exception, match="padding"):
        b64.b64u_d(unpadded)


@requires_pybase64
def test_b64u_d_padded_true_is_pinned_on_pybase64_backend() -> None:
    """Direct check that unstd doesn't inherit pybase64's own unpadded default."""
    import pybase64

    unpadded = "aGVsbG8"
    # pybase64's own new default (padded=False) would accept this — confirm unstd
    # does not silently ride that default.
    assert pybase64.urlsafe_b64decode(unpadded) == b"hello"
    with pytest.raises(Exception, match="padding"):
        b64.b64u_d(unpadded)


def test_b64u_text_round_trips_utf8() -> None:
    text = "héllo wörld 🎉"
    assert b64.b64u_text(b64.b64u_s(text.encode())) == text


# ── JSON smuggling helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize("obj", [None, True, 1, 1.5, "s", [1, 2], {"k": "v"}])
def test_b64_json_round_trips(obj: object) -> None:
    assert b64.unb64_json(b64.b64_json(obj)) == obj
