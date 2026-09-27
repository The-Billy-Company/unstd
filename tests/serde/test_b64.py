"""Adversarial + parity tests for ``unstd.serde.b64``.

Three contracts are pinned here:

1. The pybase64 backend and the stdlib ``base64`` fallback are **byte-for-byte**
   identical on encode, and agree on decode — padded *and* unpadded url-safe
   input included, which is where pybase64 ``>=1.5`` flipped its own default and
   the two backends would otherwise quietly disagree.
2. Standard decode rejects malformed input loudly (``binascii.Error``) — a
   non-alphabet byte or a missing ``=`` — and never silently truncates.
3. Url-safe decode accepts the unpadded form the alphabet is used in on the wire
   (JWTs, bearer tokens, URLs), and ``encode(pad=False)`` produces it.
"""

from __future__ import annotations

import base64

import pytest

from unstd.serde import b64


try:
    import pybase64  # noqa: F401

    _HAVE_PYBASE64 = True
except ImportError:  # base install — pybase64-only assertions are skipped
    _HAVE_PYBASE64 = False

requires_pybase64 = pytest.mark.skipif(
    not _HAVE_PYBASE64, reason="pybase64 (`serde` extra) not installed"
)

_DATA = [
    b"",
    b"h",
    b"he",
    b"hel",
    b"hell",
    b"hello world",
    b"\xfb\xff\xfe",
    bytes(range(256)),
]


@pytest.fixture(params=["pybase64", "stdlib"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run a test on each backend — the stdlib one forced even when pybase64 is present."""
    if request.param == "stdlib":
        monkeypatch.setattr(b64, "_b64", base64)
        monkeypatch.setattr(b64, "_HAVE_PYBASE64", False)
    elif not _HAVE_PYBASE64:
        pytest.skip("pybase64 (`serde` extra) not installed")
    return str(request.param)


# ── standard alphabet: byte-identical to stdlib ``base64`` ─────────────────────


@pytest.mark.parametrize("data", _DATA)
def test_encode_matches_stdlib(backend: str, data: bytes) -> None:
    assert b64.encode(data) == base64.b64encode(data).decode("ascii")


@pytest.mark.parametrize("data", _DATA)
def test_decode_round_trips_and_matches_stdlib(backend: str, data: bytes) -> None:
    s = b64.encode(data)
    assert b64.decode(s) == data == base64.b64decode(s, validate=True)
    assert b64.decode(s.encode()) == data


def test_decode_rejects_non_alphabet_bytes(backend: str) -> None:
    with pytest.raises(b64.Error):
        b64.decode("not valid base64!!")


def test_standard_decode_still_requires_padding(backend: str) -> None:
    """Leniency is the url alphabet's, not the standard one's."""
    with pytest.raises(b64.Error):
        b64.decode("aGVsbG8")


def test_decode_text_round_trips_utf8(backend: str) -> None:
    text = "héllo wörld 🎉"
    assert b64.decode_text(b64.encode(text.encode())) == text


# ── url-safe alphabet ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("data", _DATA)
def test_url_encode_matches_stdlib(backend: str, data: bytes) -> None:
    assert b64.encode(data, url=True) == base64.urlsafe_b64encode(data).decode("ascii")


@pytest.mark.parametrize("data", _DATA)
def test_unpadded_encode_is_the_padded_form_stripped(backend: str, data: bytes) -> None:
    padded = base64.urlsafe_b64encode(data).decode("ascii")
    assert b64.encode(data, url=True, pad=False) == padded.rstrip("=")
    assert b64.encode(data, pad=False) == base64.b64encode(data).decode("ascii").rstrip(
        "="
    )


@pytest.mark.parametrize("data", _DATA)
def test_url_decode_accepts_padded_and_unpadded_alike(
    backend: str, data: bytes
) -> None:
    padded = base64.urlsafe_b64encode(data)
    assert b64.decode(padded, url=True) == data == base64.urlsafe_b64decode(padded)
    assert b64.decode(padded.rstrip(b"="), url=True) == data
    assert b64.decode(padded.decode().rstrip("="), url=True) == data


def test_url_decode_rejects_an_impossible_length(backend: str) -> None:
    """One leftover character carries no whole byte — no padding can repair it."""
    with pytest.raises(ValueError):  # noqa: PT011 — binascii.Error is a ValueError
        b64.decode("a", url=True)


def test_url_decode_text_round_trips_utf8(backend: str) -> None:
    text = "héllo wörld 🎉"
    assert (
        b64.decode_text(b64.encode(text.encode(), url=True, pad=False), url=True)
        == text
    )


# ── JSON smuggling helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize("obj", [None, True, 1, 1.5, "s", [1, 2], {"k": "v"}])
def test_json_round_trips(backend: str, obj: object) -> None:
    assert b64.decode_json(b64.encode_json(obj)) == obj
