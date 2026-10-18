"""TLS policy contracts also run on the real base install without BLAKE3.

These inspect contexts and real PEM loading; full peer handshakes belong to the
penetration suite in ``assurance/security/pentest/test_tls_handshakes.py``.
"""

from __future__ import annotations

import datetime as dt
import importlib
import ssl
import sys
from typing import TYPE_CHECKING

import pytest

from unstd.crypto import tls


if TYPE_CHECKING:
    from pathlib import Path


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

    def test_combined_pem_certfile_alone_is_mtls(self, tmp_path):
        """A PEM carrying both certificate and key is the common bundle shape, so
        certfile alone must load it. The old contract dropped it on the floor, and
        the client dialed without mTLS until the peer refused the handshake.
        """
        cert_pem, key_pem = _self_signed(tmp_path, "unstd-test-bundle")
        bundle = tmp_path / "bundle.pem"
        bundle.write_bytes(cert_pem.read_bytes() + key_pem.read_bytes())
        ctx = tls.client_context(certfile=str(bundle))
        assert ctx.verify_mode.name == "CERT_REQUIRED"
        # A certificate with no key anywhere is a real half-chain, and says so.
        with pytest.raises(ssl.SSLError):
            tls.client_context(certfile=str(cert_pem))

    def test_tls_imports_without_blake3_and_digest_names_the_extra(self, monkeypatch):
        """``tls`` is pure stdlib, so a missing BLAKE3 must not take it down with the digest."""
        for name in [m for m in sys.modules if m.startswith("unstd.crypto")]:
            monkeypatch.delitem(sys.modules, name)
        monkeypatch.setitem(sys.modules, "blake3", None)
        assert importlib.import_module("unstd.crypto.tls").client_context()
        with pytest.raises(ImportError, match="'crypto' extra"):
            importlib.import_module("unstd.crypto.digest")

    def test_keyfile_without_certfile_is_refused(self, tmp_path):
        _, key_pem = _self_signed(tmp_path, "unstd-test-half")
        with pytest.raises(ValueError, match="keyfile without certfile"):
            tls.client_context(keyfile=str(key_pem))
