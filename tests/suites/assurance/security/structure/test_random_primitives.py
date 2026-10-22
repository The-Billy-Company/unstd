"""Secure draws retain stdlib secrets primitives and propagate entropy failures.

These canaries prove primitive selection and the live entropy path, not the
statistical quality of output. Outage/recovery campaigns remain in the chaos
lane; these finite checks run with ordinary security tests.
"""

from __future__ import annotations

import errno
import random
import secrets

import pytest

from unstd import rand
from unstd.rand import crypto


@pytest.mark.parametrize(
    ("name", "primitive"),
    [
        ("token_bytes", "token_bytes"),
        ("token_hex", "token_hex"),
        ("token_urlsafe", "token_urlsafe"),
        ("below", "randbelow"),
        ("bits", "randbits"),
        ("choice", "choice"),
    ],
)
def test_secure_exports_keep_secrets_primitives_across_seeding(
    name: str, primitive: str
) -> None:
    expected = getattr(secrets, primitive)
    assert getattr(crypto, name) is expected
    for _ in range(2):
        rand.seed(17)
        assert getattr(crypto, name) is expected
        assert getattr(secrets, primitive) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("token_bytes", b"Z" * 16),
        ("token_hex", "5a" * 16),
        ("token_urlsafe", "WlpaWlpaWlpaWlpaWlpaWg"),
    ],
)
def test_secure_tokens_use_live_entropy_and_fail_closed_after_seeding(
    name: str, expected: str | bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[int] = []
    failure = OSError(errno.EIO, "fixture entropy unavailable")
    armed = False

    def entropy(size: int) -> bytes:
        requests.append(size)
        if armed:
            raise failure
        return b"Z" * size

    # SystemRandom calls this stdlib provider; replacing the exported wrapper
    # would only prove that our replacement itself raises.
    monkeypatch.setattr(random, "_urandom", entropy)
    draw = getattr(crypto, name)
    assert draw(16) == expected
    assert requests == [16]

    rand.seed(17)
    armed = True
    with pytest.raises(OSError, match="fixture entropy unavailable") as caught:
        draw(16)
    assert caught.value is failure
    assert requests == [16, 16]

    armed = False
    rand.seed(17)
    assert draw(16) == expected
    assert requests == [16, 16, 16]
