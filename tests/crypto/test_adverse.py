"""Adverse tests for unstd.crypto — every way a token or MAC should be refused.

These deliberately do not mirror the implementation. Each case is a thing an
attacker or a broken caller does, with the answer written down independently: a
wrong key, a flipped claims byte, a chopped tag, a separator that is missing or
doubled, a key of the wrong width, a comparison that must not short-circuit.
"""

from __future__ import annotations

import datetime as dt
import importlib
import ssl
import sys
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from unstd.crypto import digest, tls, token


if TYPE_CHECKING:
    from pathlib import Path


_KEY = bytes(range(1, 33))
_OTHER_KEY = bytes(range(2, 34))
_CLAIMS = b'{"sub":"u1","exp":1767225600,"aud":"example"}'


def _token() -> str:
    return token.mint(_KEY, _CLAIMS)


def _self_signed(tmp_path: Path, cn: str) -> tuple[Path, Path]:
    """Mint a throwaway self-signed P-256 cert + key on disk, returning both paths.

    The TLS policy tests need real PEM because a stdlib SSLContext is the thing
    under test: nothing about ``load_cert_chain`` can be faked without also faking
    away the behavior worth pinning.
    """
    x509 = pytest.importorskip("cryptography.x509")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        # Self-signed and self-issued: a private root is exactly the shape the
        # NATS sidecar presents, and OpenSSL only files it in the trust store
        # when it says it is one.
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = tmp_path / f"{cn}.crt"
    key_pem = tmp_path / f"{cn}.key"
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_pem, key_pem


class TestWrongKey:
    def test_another_key_does_not_verify(self):
        assert token.verify(_OTHER_KEY, _token()) is None

    def test_all_zero_key_does_not_verify_a_real_token(self):
        """The fallback a mis-wired keyset produces must not open real tokens."""
        assert token.verify(bytes(token.KEY_BYTES), _token()) is None

    def test_one_flipped_key_bit_changes_the_tag(self):
        near = bytes([_KEY[0] ^ 0x01, *_KEY[1:]])
        assert token.mac_hex(near, _CLAIMS) != token.mac_hex(_KEY, _CLAIMS)
        assert not token.verify_mac(near, _CLAIMS, token.mac_hex(_KEY, _CLAIMS))

    @pytest.mark.parametrize("width", [0, 1, 16, 31, 33, 64])
    def test_wrong_key_width_is_rejected_loudly(self, width: int):
        """A short key must raise, never be silently padded to 32 bytes."""
        with pytest.raises(ValueError, match="exactly 32 bytes"):
            token.mac_hex(bytes(width), _CLAIMS)


class TestTamperedClaims:
    def test_flipping_one_claims_byte_invalidates_the_token(self):
        payload, _, tag = _token().partition(token.SEPARATOR)
        flipped = ("A" if payload[0] != "A" else "B") + payload[1:]
        assert token.verify(_KEY, flipped + token.SEPARATOR + tag) is None

    def test_swapping_a_tag_between_two_tokens_fails(self):
        a_payload = _token().partition(token.SEPARATOR)[0]
        b_tag = token.mint(_KEY, b'{"sub":"u2"}').partition(token.SEPARATOR)[2]
        assert token.verify(_KEY, a_payload + token.SEPARATOR + b_tag) is None

    def test_an_empty_claims_payload_is_refused(self):
        _, _, tag = _token().partition(token.SEPARATOR)
        assert token.verify(_KEY, token.SEPARATOR + tag) is None

    def test_re_encoding_the_claims_differently_still_fails(self):
        """The MAC is over the payload string, so a re-spelled payload is a tamper.

        Unpadded base64url of a 41-byte document has no trailing ``=``; adding one
        decodes to the same claims but is a different payload string, and so must
        not verify under the original tag.
        """
        payload, _, tag = _token().partition(token.SEPARATOR)
        assert token.verify(_KEY, payload + "=" + token.SEPARATOR + tag) is None


class TestTruncatedOrMalformedTag:
    @pytest.mark.parametrize("chop", [1, 2, 4, 10, 43])
    def test_truncated_tag_is_refused(self, chop: int):
        tok = _token()
        assert token.verify(_KEY, tok[:-chop]) is None

    def test_tag_of_the_wrong_width_is_refused_before_comparison(self):
        payload = _token().partition(token.SEPARATOR)[0]
        short = token.mac_raw(_KEY, payload.encode())[:16]
        import base64

        wrong = base64.urlsafe_b64encode(short).decode().rstrip("=")
        assert token.verify(_KEY, payload + token.SEPARATOR + wrong) is None

    def test_empty_tag_is_refused(self):
        payload = _token().partition(token.SEPARATOR)[0]
        assert token.verify(_KEY, payload + token.SEPARATOR) is None

    def test_non_base64_tag_is_refused_without_raising(self):
        payload = _token().partition(token.SEPARATOR)[0]
        assert (
            token.verify(_KEY, payload + token.SEPARATOR + "!!!!not base64!!!!") is None
        )


class TestSeparator:
    def test_missing_separator_is_refused(self):
        assert token.verify(_KEY, _token().replace(token.SEPARATOR, "", 1)) is None

    def test_extra_separator_is_refused(self):
        assert token.verify(_KEY, _token() + token.SEPARATOR + "extra") is None

    def test_separator_inside_the_payload_is_refused(self):
        payload, _, tag = _token().partition(token.SEPARATOR)
        assert (
            token.verify(_KEY, f"{payload}{token.SEPARATOR}x{token.SEPARATOR}{tag}")
            is None
        )

    def test_bare_separator_is_refused(self):
        for junk in ("", ".", "..", "a", ".a", "a."):
            assert token.verify(_KEY, junk) is None

    def test_verify_never_raises_on_caller_supplied_junk(self):
        """Attacker-controlled input must not turn a 401 into a 500."""
        for junk in ("\x00", "ü.ü", "a" * 4096, "." * 100, "=.=", "-.-"):
            assert token.verify(_KEY, junk) is None


class TestConstantTimeCompare:
    def test_equal_is_reflexive_and_rejects_a_near_miss(self):
        tag = token.mac_hex(_KEY, _CLAIMS)
        assert token.equal(tag, tag)
        assert not token.equal(tag, tag[:-1] + ("0" if tag[-1] != "0" else "1"))

    def test_prefix_agreement_does_not_admit(self):
        """A tag sharing all but the last byte must be as rejected as random junk.

        This is the shape a byte-at-a-time forgery walks: it needs the compare to
        answer differently for a longer shared prefix. It cannot, because it never
        answers True for anything but the whole tag.
        """
        tag = token.mac_hex(_KEY, _CLAIMS)
        for keep in range(len(tag)):
            assert not token.equal(tag, tag[:keep])

    def test_mismatched_types_are_false_not_a_typeerror(self):
        assert not token.equal("abc", b"abc")
        assert not token.equal(b"abc", "abc")

    def test_equal_routes_through_hmac_compare_digest(self):
        """The one regression this file can express structurally.

        ``hmac.compare_digest`` is the only constant-time compare in the stdlib. If
        someone rewrote ``equal`` as ``a == b`` the timing leak would be invisible
        in review and silent at runtime, so pin the call itself.
        """
        import inspect

        source = inspect.getsource(token.equal)
        assert "hmac.compare_digest" in source
        assert "==" not in source.split('"""')[-1]

    def test_verify_mac_uses_the_constant_time_path(self):
        import inspect

        assert "equal(" in inspect.getsource(token.verify_mac)


class TestDigestGuards:
    @pytest.mark.parametrize("n", [0, -1, 33, 64])
    def test_truncation_width_out_of_range_raises(self, n: int):
        with pytest.raises(ValueError, match="truncation width"):
            digest.hex(b"abc", n)

    def test_derive_key_rejects_nothing_but_still_separates(self):
        """``derive_key`` takes any context string; registering one is the caller's job.

        The guard that matters is not a type check here - it is that two different
        strings never collide, including a one-character difference.
        """
        a = digest.derive_key("unstd adverse v1", b"m")
        b = digest.derive_key("unstd adverse v2", b"m")
        assert a != b
        assert len(a) == len(b) == digest.DIGEST_BYTES

    def test_legacy_blake2b_is_a_different_algorithm_at_every_width(self):
        """The escape hatch must stay an escape hatch — never alias the one digest."""
        for width in (16, 32, 64):
            got = digest.legacy_blake2b(b"abc", width=width)
            assert len(got) == width
            assert got != digest.raw(b"abc")[:width]

    def test_stream_stays_usable_after_reading(self):
        s = digest.Stream().update(b"a")
        first = s.hex()
        assert s.update(b"bc").hex() == digest.hex(b"abc")
        assert first == digest.hex(b"a")


class TestTlsPolicy:
    def test_client_context_verifies_by_default(self):
        ctx = tls.client_context()
        assert ctx.check_hostname is True
        assert ctx.verify_mode.name == "CERT_REQUIRED"
        assert ctx.minimum_version is tls.MINIMUM_VERSION

    def test_there_is_no_way_to_ask_for_an_unverified_context(self):
        import inspect

        params = set(inspect.signature(tls.client_context).parameters)
        assert params == {"cafile", "certfile", "keyfile", "minimum_version"}

    def test_the_floor_is_tls_1_3(self):
        """A silently lowered floor is the whole downgrade class, so pin the value."""
        assert tls.MINIMUM_VERSION is ssl.TLSVersion.TLSv1_3

    def test_the_legacy_floor_is_named_and_is_still_a_floor(self):
        """Dialing a 1.2-only peer is a stated exception, not a free-form version."""
        assert tls.LEGACY_MINIMUM_VERSION is ssl.TLSVersion.TLSv1_2
        ctx = tls.client_context(minimum_version=tls.LEGACY_MINIMUM_VERSION)
        assert ctx.minimum_version is ssl.TLSVersion.TLSv1_2
        assert ctx.check_hostname is True
        assert ctx.verify_mode.name == "CERT_REQUIRED"

    @pytest.mark.parametrize(
        "version", [ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1_1, ssl.TLSVersion.SSLv3]
    )
    def test_a_floor_below_tls_1_2_is_refused_rather_than_honored(self, version):
        """The dial exists for 1.2-only peers, and stops there.

        A caller reaching past it wants a downgrade, and getting one quietly is
        exactly the failure the seam exists to prevent — so the context is never
        built at all.
        """
        with pytest.raises(ValueError, match="below TLSv1_2"):
            tls.client_context(minimum_version=version)

    def test_a_pinned_root_narrows_trust_rather_than_widening_it(self, tmp_path):
        """A private CA must be *added* to nothing, not appended to the public set.

        Pinning a self-signed sidecar root is the branch that decides whether a
        caller also quietly keeps trusting every public CA.
        """
        ca_pem, _ = _self_signed(tmp_path, "unstd-test-ca")
        default = tls.client_context()
        pinned = tls.client_context(cafile=str(ca_pem))
        assert len(pinned.get_ca_certs()) == 1
        assert (
            len(default.get_ca_certs()) != 1
            or default.get_ca_certs() != pinned.get_ca_certs()
        )
        assert pinned.check_hostname is True

    def test_mtls_loads_the_chain_and_refuses_a_mismatched_pair(self, tmp_path):
        """Certfile + keyfile is mTLS, and the proof that the chain is really
        loaded is that a key which does not match its certificate is *rejected* —
        an unloaded chain would accept anything, then fail at handshake time on
        somebody else's machine.
        """
        cert_pem, key_pem = _self_signed(tmp_path, "unstd-test-client")
        tls.client_context(certfile=str(cert_pem), keyfile=str(key_pem))

        _, other_key = _self_signed(tmp_path, "unstd-test-other")
        with pytest.raises(ssl.SSLError):
            tls.client_context(certfile=str(cert_pem), keyfile=str(other_key))

    def test_half_configured_mtls_yields_a_plain_verifying_context(self, tmp_path):
        """One half alone is not mTLS. It must not load a partial chain, and it
        must not raise either — the caller gets the ordinary verifying policy.
        """
        cert_pem, key_pem = _self_signed(tmp_path, "unstd-test-half")
        for kwargs in ({"certfile": str(cert_pem)}, {"keyfile": str(key_pem)}):
            ctx = tls.client_context(**kwargs)
            assert ctx.minimum_version is tls.MINIMUM_VERSION
            assert ctx.verify_mode.name == "CERT_REQUIRED"

    def test_tls_imports_without_blake3_and_digest_names_the_extra(self, monkeypatch):
        """``tls`` is pure stdlib, so a missing BLAKE3 must not take it down with the digest."""
        for name in [m for m in sys.modules if m.startswith("unstd.crypto")]:
            monkeypatch.delitem(sys.modules, name)
        monkeypatch.setitem(sys.modules, "blake3", None)
        assert importlib.import_module("unstd.crypto.tls").client_context()
        with pytest.raises(ImportError, match="'crypto' extra"):
            importlib.import_module("unstd.crypto.digest")


class TestRoundTripProperties:
    @given(
        claims=st.binary(min_size=1, max_size=2048),
        key=st.binary(min_size=32, max_size=32),
    )
    @settings(deadline=None)
    def test_mint_then_verify_returns_the_claims(self, claims: bytes, key: bytes):
        assert token.verify(key, token.mint(key, claims)) == claims

    def test_minting_empty_claims_raises_rather_than_returning_a_dead_token(self):
        """``verify`` refuses an empty claims segment, so ``mint`` must not produce one."""
        with pytest.raises(ValueError, match="no claims"):
            token.mint(_KEY, b"")

    @given(
        claims=st.binary(min_size=1, max_size=256),
        key=st.binary(min_size=32, max_size=32),
        other=st.binary(min_size=32, max_size=32),
    )
    @settings(deadline=None)
    def test_a_different_key_never_verifies(
        self, claims: bytes, key: bytes, other: bytes
    ):
        if key == other:
            return
        assert token.verify(other, token.mint(key, claims)) is None

    @given(data=st.binary(max_size=1024))
    @settings(deadline=None)
    def test_truncated_and_streamed_digests_agree_with_the_one_shot(self, data: bytes):
        """``hex(data, n)`` asks the XOF for *n* bytes rather than slicing — it must
        still be the prefix of the full digest, and ``Stream(parts)`` must be the
        digest of the parts joined.
        """
        full = digest.hex(data)
        assert all(digest.hex(data, n) == full[: n * 2] for n in (1, 8, 16, 32))
        assert digest.raw(data) == bytes.fromhex(full)
        cut = len(data) // 2
        assert digest.Stream([data[:cut], data[cut:]]).raw() == digest.raw(data)
        assert digest.Stream([data]).hex(8) == digest.hex(data, 8)
