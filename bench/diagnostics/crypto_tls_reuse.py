"""Paired verified TLS 1.3 handshakes with fresh and caller-owned contexts.

MemoryBIO removes sockets, worker creation and network scheduling from this
context-ownership comparison. This is setup/handshake evidence, not network or
record-throughput evidence. Every connection has fresh SSLObjects, no session
argument and no server tickets; certificate and hostname checks stay enabled.
"""

from __future__ import annotations

import _ssl
import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import gc
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import ssl
import statistics
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives import serialization

from tests.support.security.tls import CertificateAuthority, Identity, server_context
from tests.tooling.runtime.tls import runtime_report
from unstd.crypto import tls


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Policy:
    """The same private trust and optional client identity on both sides."""

    name: str
    authority: CertificateAuthority
    server_identity: Identity
    client_identity: Identity | None = None

    def context(self) -> ssl.SSLContext:
        """The complete client factory/configuration operation being compared."""
        context = tls.client_context(
            cafile=str(self.authority.root.certfile),
            certfile=str(self.client_identity.certfile)
            if self.client_identity
            else None,
            keyfile=str(self.client_identity.keyfile) if self.client_identity else None,
        )
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        return context


def _handshake(
    context: ssl.SSLContext,
    server: ssl.SSLContext,
    *,
    hostname: str = "localhost",
    proof: dict[str, Any] | None = None,
) -> None:
    """Complete a bounded fresh two-peer handshake, including certificate checks."""
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = context.wrap_bio(
        client_in, client_out, server_side=False, server_hostname=hostname
    )
    peer = server.wrap_bio(server_in, server_out, server_side=True)
    client_done = server_done = False
    wire = bytearray() if proof is not None else None
    for _ in range(64):
        if not client_done:
            try:
                client.do_handshake()
                client_done = True
            except ssl.SSLWantReadError:
                pass
        data = client_out.read()
        if data:
            server_in.write(data)
        if not server_done:
            try:
                peer.do_handshake()
                server_done = True
            except ssl.SSLWantReadError:
                pass
        data = server_out.read()
        if data:
            client_in.write(data)
            if wire is not None:
                wire.extend(data)
        if client_done and server_done:
            break
    else:
        message = "TLS MemoryBIO peers exceeded 64 handshake steps"
        raise RuntimeError(message)
    assert client.version() == peer.version() == "TLSv1.3"
    assert not client.session_reused
    assert not peer.session_reused
    if proof is not None:
        assert wire is not None
        server_certificate = client.getpeercert(binary_form=True)
        client_certificate = peer.getpeercert(binary_form=True)
        assert server_certificate is not None
        proof.update(
            {
                "version": client.version(),
                "cipher": client.cipher(),
                "selected_group": _server_hello_group(wire),
                "server_certificate_sha256": hashlib.sha256(
                    server_certificate
                ).hexdigest(),
                "client_certificate_sha256": hashlib.sha256(
                    client_certificate
                ).hexdigest()
                if client_certificate
                else None,
                "client_session_reused": client.session_reused,
                "server_session_reused": peer.session_reused,
            }
        )


def _server_hello_group(wire: bytearray) -> str:
    """Read the negotiated group from the plaintext ServerHello key-share extension."""
    handshake = bytearray()
    offset = 0
    while offset + 5 <= len(wire):
        size = int.from_bytes(wire[offset + 3 : offset + 5])
        end = offset + 5 + size
        assert end <= len(wire)
        if wire[offset] == 22:
            handshake.extend(wire[offset + 5 : end])
        offset = end
    assert handshake[0] == 2  # ServerHello, before encrypted handshake records.
    end = 4 + int.from_bytes(handshake[1:4])
    assert end <= len(handshake)
    offset = 4 + 2 + 32  # Handshake header, legacy version, random.
    offset += 1 + handshake[offset]  # Legacy session ID.
    offset += 2 + 1  # Cipher suite, legacy compression.
    extension_end = offset + 2 + int.from_bytes(handshake[offset : offset + 2])
    offset += 2
    assert extension_end == end
    while offset + 4 <= extension_end:
        kind = int.from_bytes(handshake[offset : offset + 2])
        size = int.from_bytes(handshake[offset + 2 : offset + 4])
        offset += 4
        assert offset + size <= extension_end
        if kind == 51:
            assert size >= 2
            return f"0x{int.from_bytes(handshake[offset : offset + 2]):04x}"
        offset += size
    message = "TLS 1.3 ServerHello did not include a key-share group"
    raise RuntimeError(message)


def _elapsed(function: Callable[[], None], repetitions: int) -> dict[str, int]:
    """Complete batch wall and process CPU times, with the same Python loop."""
    wall = time.perf_counter_ns()
    cpu = time.process_time_ns()
    for _ in range(repetitions):
        function()
    return {
        "wall_ns": time.perf_counter_ns() - wall,
        "cpu_ns": time.process_time_ns() - cpu,
    }


def _summary(samples: list[dict[str, int]], repetitions: int) -> dict[str, Any]:
    """All raw samples and robust batch-mean summaries, never latency percentiles."""
    result: dict[str, Any] = {"raw_batches": samples}
    for clock in ("wall_ns", "cpu_ns"):
        values = [sample[clock] / repetitions for sample in samples]
        median = statistics.median(values)
        result[clock] = {
            "median_per_operation": median,
            "minimum_per_operation": min(values),
            "maximum_per_operation": max(values),
            "relative_mad": statistics.median(abs(value - median) for value in values)
            / median,
        }
    return result


def _load_average() -> tuple[float, float, float] | None:
    """Optional host load metadata on platforms that expose it."""
    if sys.platform == "win32":
        return None
    try:
        return os.getloadavg()
    except (AttributeError, OSError):
        return None


def _paired(
    fresh: Callable[[], None],
    reused: Callable[[], None],
    *,
    rounds: int,
    repetitions: int,
    setup: dict[str, int] | None,
    first: int,
    prepare_fresh: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Alternate batch order; configured first-use contexts stay outside timers."""
    samples: dict[str, list[dict[str, int]]] = {"fresh": [], "reused": []}
    for index in range(rounds):
        order = ("fresh", "reused") if (index + first) % 2 == 0 else ("reused", "fresh")
        for name in order:
            if name == "fresh" and prepare_fresh is not None:
                prepare_fresh()
            samples[name].append(
                _elapsed(fresh if name == "fresh" else reused, repetitions)
            )
    result: dict[str, Any] = {
        "rounds": rounds,
        "operations_per_batch": repetitions,
        "first_pair_order": ["fresh", "reused"]
        if first % 2 == 0
        else ["reused", "fresh"],
        "fresh": _summary(samples["fresh"], repetitions),
        "reused": _summary(samples["reused"], repetitions),
    }
    for clock in ("wall_ns", "cpu_ns"):
        ratios = [
            old[clock] / new[clock]
            for old, new in zip(samples["fresh"], samples["reused"], strict=True)
        ]
        result[clock] = {
            "paired_ratios": ratios,
            "median_paired_speedup": statistics.median(ratios),
        }
        if setup is not None:
            result[clock]["median_speedup_with_one_setup_per_batch"] = (
                statistics.median(
                    old[clock] / (new[clock] + setup[clock])
                    for old, new in zip(
                        samples["fresh"], samples["reused"], strict=True
                    )
                )
            )
    return result


def _profile(
    policy: Policy, rounds: int, repetitions: int, first: int
) -> dict[str, Any]:
    """Prove equivalent verified policy before measuring both ownership choices."""
    server = server_context(
        policy.server_identity,
        client_ca=policy.authority.root if policy.client_identity else None,
        maximum=ssl.TLSVersion.TLSv1_3,
    )
    server.num_tickets = 0
    wall, cpu = time.perf_counter_ns(), time.process_time_ns()
    reused = policy.context()
    setup = {
        "wall_ns": time.perf_counter_ns() - wall,
        "cpu_ns": time.process_time_ns() - cpu,
    }
    assert reused.check_hostname
    assert reused.verify_mode == ssl.CERT_REQUIRED
    assert len(reused.get_ca_certs()) == 1
    fresh_proof: dict[str, Any] = {}
    reused_proof: dict[str, Any] = {}
    _handshake(policy.context(), server, proof=fresh_proof)
    _handshake(reused, server, proof=reused_proof)
    assert fresh_proof == reused_proof
    assert (
        fresh_proof["server_certificate_sha256"]
        == hashlib.sha256(
            policy.server_identity.certificate.public_bytes(serialization.Encoding.DER)
        ).hexdigest()
    )
    if policy.client_identity:
        assert (
            fresh_proof["client_certificate_sha256"]
            == hashlib.sha256(
                policy.client_identity.certificate.public_bytes(
                    serialization.Encoding.DER
                )
            ).hexdigest()
        )
    rejected: list[str] = []
    for kind, context, hostname in (
        ("hostname", reused, "attacker.test"),
        ("untrusted-root", tls.client_context(), "localhost"),
    ):
        try:
            _handshake(context, server, hostname=hostname)
        except ssl.SSLCertVerificationError:
            rejected.append(kind)
        else:
            message = f"TLS benchmark failed to reject {kind}"
            raise RuntimeError(message)

    def fresh() -> None:
        _handshake(policy.context(), server)

    def shared() -> None:
        _handshake(reused, server)

    def factory() -> None:
        policy.context()

    contexts: list[ssl.SSLContext] = []
    context_index = 0

    def prepare_fresh() -> None:
        nonlocal contexts, context_index
        contexts = [policy.context() for _ in range(repetitions)]
        context_index = 0

    def first_use_handshake() -> None:
        nonlocal context_index
        _handshake(contexts[context_index], server)
        context_index += 1

    enabled = gc.isenabled()
    gc.disable()
    try:
        factory_samples = [_elapsed(factory, repetitions) for _ in range(rounds)]
        complete = _paired(
            fresh,
            shared,
            rounds=rounds,
            repetitions=repetitions,
            setup=setup,
            first=first,
        )
        handshake_only = _paired(
            first_use_handshake,
            shared,
            rounds=rounds,
            repetitions=repetitions,
            setup=None,
            first=first + 1,
            prepare_fresh=prepare_fresh,
        )
    finally:
        if enabled:
            gc.enable()
    return {
        "name": policy.name,
        "security_proof": fresh_proof,
        "negative_controls_rejected": rejected,
        "caller_owned_context_setup": setup,
        "factory_only_including_context_release": _summary(
            factory_samples, repetitions
        ),
        "complete_factory_and_verified_handshake": complete,
        "verified_handshake_only_contexts_prepared_outside_timer": handshake_only,
    }


def main() -> None:
    """Record reproducible local ownership evidence without production mutations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=11)
    parser.add_argument("--repetitions", type=int, default=64)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("bench/audits/crypto/tls-reuse-results.json"),
    )
    args = parser.parse_args()
    if args.rounds < 3 or args.repetitions < 1:
        parser.error("at least three rounds and positive repetitions are required")
    runtime = runtime_report()
    if runtime["errors"]:
        parser.error(f"TLS runtime preflight failed: {runtime['errors']}")
    root = Path(__file__).resolve().parents[2]
    paths = [
        Path(__file__),
        root / "src/unstd/crypto/tls.py",
        root / "tests/support/security/tls.py",
        root / "tests/tooling/runtime/tls.py",
    ]
    report: dict[str, Any] = {
        "created_utc": datetime.now(UTC).isoformat(),
        "runtime": runtime,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cryptography": metadata.version("cryptography"),
        "cpu_count": os.cpu_count(),
        "load_average_before": _load_average(),
        "source_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
        "ssl_module_sha256": hashlib.sha256(
            Path(ssl.__file__).read_bytes()
        ).hexdigest(),
        "ssl_extension": {
            "origin": getattr(_ssl, "__file__", "built-in"),
            "sha256": hashlib.sha256(
                Path(getattr(_ssl, "__file__", sys.executable)).read_bytes()
            ).hexdigest(),
            "hash_scope": "extension file, or interpreter binary when _ssl is built in",
        },
        "method": {
            "transport": "MemoryBIO; no socket/network/thread creation",
            "server_context": "one equally prepared context per profile; num_tickets=0",
            "client_session": "fresh SSLObject each operation; no session passed; both session_reused flags asserted false",
            "verification": "CERT_REQUIRED plus hostname; pinned private root; separate wrong-hostname/untrusted-root negative controls",
            "policy_equivalence": "same factory, root, identity, TLS1.3 min/max, native default ciphers/groups; selected group/cipher/cert hashes compared before timing",
            "rounds": args.rounds,
            "operations_per_batch": args.repetitions,
            "clocks": "perf_counter_ns and process_time_ns; calibration and proof outside timers",
            "garbage_collection": "disabled equally for both timed paths; reference-counted SSLObject/context release retained",
            "setup_accounting": "one caller-owned context setup measured separately; complete comparison also reports adding that setup to every reused batch",
            "limitations": "single machine/interpreter; warmed same ephemeral PEM files; MemoryBIO handshakes do not measure network latency, wire overhead or record throughput; batch means are not per-handshake latency distributions",
        },
    }
    with tempfile.TemporaryDirectory(prefix="unstd-tls-reuse-") as directory:
        authority = CertificateAuthority(Path(directory), "reuse-root")
        identity = authority.issue("reuse-server")
        client = authority.issue("reuse-client", purpose="client")
        report["profiles"] = [
            _profile(policy, args.rounds, args.repetitions, index)
            for index, policy in enumerate(
                (
                    Policy("pinned-server-authentication", authority, identity),
                    Policy("mutual-tls", authority, identity, client),
                )
            )
        ]
    report["load_average_after"] = _load_average()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for profile in report["profiles"]:
        comparison = profile["complete_factory_and_verified_handshake"]
        wall = comparison["wall_ns"]["median_paired_speedup"]
        cpu = comparison["cpu_ns"]["median_paired_speedup"]
        print(f"{profile['name']}: wall {wall:.3f}x; CPU {cpu:.3f}x")
    print(f"Evidence: {args.output}")


if __name__ == "__main__":
    main()
