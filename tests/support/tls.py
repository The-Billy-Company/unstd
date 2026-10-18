"""Independent PKI and bounded real loopback peers for TLS penetration tests.

Every certificate is issued by the fixture, never by the transport policy under
test. An exchange owns one listener, one worker and one accepted connection;
timeouts and unconditional joins keep rejected handshakes from leaking peers.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import datetime as dt
import ipaddress
import socket
import ssl
import threading
import time
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


if TYPE_CHECKING:
    from pathlib import Path


_EXCHANGE_TIMEOUT = 5.0
_JOIN_TIMEOUT = 5.0
REQUEST = b"unstd authenticated request\x00\xff"
RESPONSE = b"unstd authenticated response\xff\x00"


@dataclass(frozen=True)
class Identity:
    certificate: x509.Certificate
    key: ec.EllipticCurvePrivateKey
    certfile: Path
    keyfile: Path
    bundle: Path


class CertificateAuthority:
    """A throwaway root and optional intermediate, with separately keyed leaves."""

    def __init__(
        self, directory: Path, name: str, *, intermediate: bool = True
    ) -> None:
        self.directory = directory
        self.now = dt.datetime.now(dt.UTC)
        self.root = self._issue(name, ca=True)
        self.issuer = (
            self._issue(f"{name}-intermediate", ca=True, issuer=self.root)
            if intermediate
            else self.root
        )

    def issue(
        self,
        name: str,
        *,
        purpose: str = "server",
        dns_names: tuple[str, ...] = ("localhost",),
        ip_addresses: tuple[str, ...] = ("127.0.0.1",),
        not_before: dt.timedelta = dt.timedelta(days=-1),
        not_after: dt.timedelta = dt.timedelta(days=1),
    ) -> Identity:
        return self._issue(
            name,
            ca=False,
            issuer=self.issuer,
            purpose=purpose,
            dns_names=dns_names,
            ip_addresses=ip_addresses,
            not_before=not_before,
            not_after=not_after,
        )

    def _issue(
        self,
        name: str,
        *,
        ca: bool,
        issuer: Identity | None = None,
        purpose: str = "server",
        dns_names: tuple[str, ...] = (),
        ip_addresses: tuple[str, ...] = (),
        not_before: dt.timedelta = dt.timedelta(days=-2),
        not_after: dt.timedelta = dt.timedelta(days=2),
    ) -> Identity:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer.certificate.subject if issuer else subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(self.now + not_before)
            .not_valid_after(self.now + not_after)
            .add_extension(
                x509.BasicConstraints(ca=ca, path_length=1 if ca else None),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=ca,
                    crl_sign=ca,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    issuer.key.public_key() if issuer else key.public_key()
                ),
                critical=False,
            )
        )
        if not ca:
            eku = (
                ExtendedKeyUsageOID.SERVER_AUTH
                if purpose == "server"
                else ExtendedKeyUsageOID.CLIENT_AUTH
            )
            builder = builder.add_extension(x509.ExtendedKeyUsage([eku]), critical=True)
            names: list[x509.GeneralName] = [x509.DNSName(value) for value in dns_names]
            names.extend(
                x509.IPAddress(ipaddress.ip_address(value)) for value in ip_addresses
            )
            if names:
                builder = builder.add_extension(
                    x509.SubjectAlternativeName(names), critical=False
                )
        certificate = builder.sign(issuer.key if issuer else key, hashes.SHA256())
        pem = certificate.public_bytes(serialization.Encoding.PEM)
        chain = pem
        if issuer and issuer is not self.root:
            chain += issuer.certificate.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        certfile = self.directory / f"{name}.crt"
        keyfile = self.directory / f"{name}.key"
        bundle = self.directory / f"{name}.pem"
        certfile.write_bytes(chain)
        keyfile.write_bytes(key_pem)
        bundle.write_bytes(chain + key_pem)
        return Identity(certificate, key, certfile, keyfile, bundle)


def server_context(
    identity: Identity,
    *,
    client_ca: Identity | None = None,
    minimum: ssl.TLSVersion = ssl.TLSVersion.TLSv1_3,
    maximum: ssl.TLSVersion = ssl.TLSVersion.MAXIMUM_SUPPORTED,
) -> ssl.SSLContext:
    """An independent stdlib server, optionally requiring verified client identity."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = minimum
    context.maximum_version = maximum
    context.load_cert_chain(str(identity.certfile), str(identity.keyfile))
    if client_ca:
        context.load_verify_locations(cafile=str(client_ca.certfile))
        context.verify_mode = ssl.CERT_REQUIRED
    return context


@dataclass
class Exchange:
    client_error: OSError | None = None
    server_error: OSError | None = None
    client_version: str | None = None
    server_version: str | None = None
    server_certificate: bytes | None = None
    client_certificate: bytes | None = None
    request: bytes | None = None
    response: bytes | None = None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        msg = "TLS fixture exceeded its exchange deadline"
        raise TimeoutError(msg)
    return remaining


def _receive(connection: ssl.SSLSocket, size: int, deadline: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def exchange(
    client: ssl.SSLContext,
    server: ssl.SSLContext,
    *,
    hostname: str = "localhost",
) -> Exchange:
    """One authenticated request/response, preserving each peer's actual failure.

    Listening before starting the worker is the readiness signal: the ephemeral
    port can already accept the client's TCP connection. All I/O has a deadline,
    and the parent refuses to return until the fixture-owned worker has exited.
    """
    result = Exchange()
    deadline = time.monotonic() + _EXCHANGE_TIMEOUT
    active: socket.socket | None = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.settimeout(_remaining(deadline))
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        address = listener.getsockname()

        def serve() -> None:
            nonlocal active
            try:
                raw, _ = listener.accept()
                active = raw
                with raw:
                    raw.settimeout(_remaining(deadline))
                    with server.wrap_socket(
                        raw, server_side=True, do_handshake_on_connect=False
                    ) as connection:
                        active = connection
                        connection.do_handshake()
                        result.server_version = connection.version()
                        result.client_certificate = connection.getpeercert(
                            binary_form=True
                        )
                        result.request = _receive(connection, len(REQUEST), deadline)
                        if result.request == REQUEST:
                            connection.settimeout(_remaining(deadline))
                            connection.sendall(RESPONSE)
            except OSError as error:
                result.server_error = error

        worker = threading.Thread(target=serve, name="unstd-test-tls-peer")
        worker.start()
        try:
            try:
                with (
                    socket.create_connection(
                        address, timeout=_remaining(deadline)
                    ) as raw,
                    client.wrap_socket(
                        raw, server_hostname=hostname, do_handshake_on_connect=False
                    ) as connection,
                ):
                    connection.settimeout(_remaining(deadline))
                    connection.do_handshake()
                    result.client_version = connection.version()
                    result.server_certificate = connection.getpeercert(binary_form=True)
                    connection.settimeout(_remaining(deadline))
                    connection.sendall(REQUEST)
                    result.response = _receive(connection, len(RESPONSE), deadline)
            except OSError as error:
                result.client_error = error
        finally:
            worker.join(_JOIN_TIMEOUT)
            if worker.is_alive():
                listener.close()
                if active is not None:
                    with suppress(OSError):
                        active.shutdown(socket.SHUT_RDWR)
                    active.close()
                worker.join(_JOIN_TIMEOUT)
                if worker.is_alive():
                    msg = "TLS fixture failed to reap its bounded peer"
                    raise RuntimeError(msg)
    return result
