"""Output-checked backend experiments; never changes production dispatch.

Run with the crypto/serde development environment. Optional PyCryptodome may
be installed in a separate directory passed using --candidate-path. Timings
reuse crypto_hash's alternating batches and retain every observation. Ratios
are current/candidate time: above one favors the candidate. These are local
experiments, not a portable no-regression guarantee.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from functools import partial
import hashlib
import importlib
from importlib import metadata, util
import json
import math
import os
from pathlib import Path
import platform
import secrets
import ssl
import sys
from typing import Any

from bench.crypto_hash import Workload, _load, _load_average, _measure, _source_hashes
from bench.speed_cases_runtime import _crypto
from bench.timing import measure
from unstd.crypto import digest, token
from unstd.rand import crypto as secure
from unstd.serde import b64


_KEY = bytes(range(32))
_CONTEXT = "unstd crypto backend evaluation v1"


def _stdlib_codec() -> Any:
    """Load a separate codec instance with its documented stdlib fallback."""
    spec = util.spec_from_file_location("_crypto_audit_b64", b64.__file__)
    if spec is None or spec.loader is None:
        msg = "cannot load isolated base64 codec"
        raise RuntimeError(msg)
    module: Any = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._HAVE_PYBASE64 = False
    return module


def _stream_raw(stream_type: Any, chunks: tuple[bytes, ...]) -> Any:
    """Finalize one stream without input allocation in the timer."""
    return stream_type(chunks).raw()


def _blake2_raw(backend: Any, data: bytes, width: int) -> Any:
    """Finalize the candidate with the same BLAKE2 digest-size parameter."""
    return backend.new(data=data, digest_bytes=width).digest()


def _cases(source: Path, c_path: Path | None) -> list[tuple[Workload, str]]:
    """Prepare same-algorithm candidates outside timing, including all modes."""
    serial_digest, serial_token = _load(source)
    serial_digest._PARALLEL_MIN_BYTES = sys.maxsize
    serial_token._PARALLEL_MIN_BYTES = sys.maxsize
    python_digest, _ = _load(source)
    python_digest._native_absorb = None
    _, stdlib_token = _load(source)
    stdlib_token.b64 = _stdlib_codec()
    c_modules = None
    if c_path is not None:
        spec = util.spec_from_file_location("blake3", c_path)
        if spec is None or spec.loader is None:
            msg = "cannot load experimental BLAKE3 C module"
            raise RuntimeError(msg)
        c_module: Any = util.module_from_spec(spec)
        spec.loader.exec_module(c_module)
        c_digest, c_token = _load(source)
        c_digest.blake3 = c_token.blake3 = c_module.blake3
        sys.modules[c_digest.__package__ + "._blake3"].__dict__["blake3"] = (
            c_module.blake3
        )
        c_modules = (c_digest, c_token)
    cases = []
    for size in (64, 1024, 256 << 10, 1 << 20, 2 << 20, 4 << 20, 16 << 20, 64 << 20):
        data = (bytes(range(256)) * ((size + 255) // 256))[:size]
        for name in ("raw", "hex", "derive_key", "mac_raw", "mac_hex"):
            old = token if name.startswith("mac_") else digest
            new = serial_token if name.startswith("mac_") else serial_digest
            args = (
                (_KEY, data)
                if name.startswith("mac_")
                else (_CONTEXT, data)
                if name == "derive_key"
                else (data,)
            )
            cases.append(
                (
                    Workload(
                        f"{name}/{size}/serial",
                        size,
                        partial(getattr(old, name), *args),
                        partial(getattr(new, name), *args),
                    ),
                    "Identical public wrapper with parallel threshold disabled",
                )
            )
            if c_modules is not None and size in (64, 1024, 1 << 20, 64 << 20):
                candidate = c_modules[name.startswith("mac_")]
                cases.append(
                    (
                        Workload(
                            f"{name}/{size}/experimental-c",
                            size,
                            partial(getattr(old, name), *args),
                            partial(getattr(candidate, name), *args),
                        ),
                        "Same public wrapper, experimental C backend; max_threads ignored upstream; API incompatibilities evaluated separately",
                    )
                )
    for size, chunk in (
        (0, 1),
        (64, 64),
        (64, 8),
        (64 << 10, 1),
        (1 << 20, 64),
        (1 << 20, 1024),
        (1 << 20, 4096),
    ):
        chunks = tuple(b"x" * min(chunk, size - at) for at in range(0, size, chunk))
        cases.append(
            (
                Workload(
                    f"Stream/{size}/{chunk}/python-loop",
                    size,
                    partial(_stream_raw, digest.Stream, chunks),
                    partial(_stream_raw, python_digest.Stream, chunks),
                ),
                "Same BLAKE3, optional native batching disabled in isolated module",
            )
        )
    try:
        candidate_blake2b = importlib.import_module("Crypto.Hash.BLAKE2b")
    except ImportError:
        candidate_blake2b = None
    from cryptography.hazmat.primitives import hashes

    for size in (64, 1024, 1 << 20):
        data = b"x" * size
        if candidate_blake2b is not None:
            cases.extend(
                (
                    Workload(
                        f"legacy_blake2b/{size}/{width}/pycryptodome",
                        size,
                        partial(digest.legacy_blake2b, data, width=width),
                        partial(_blake2_raw, candidate_blake2b, data, width),
                    ),
                    "Same BLAKE2b digest-size parameter; dependency not installed in project",
                )
                for width in (1, 32, 64)
            )
        algorithm = hashes.BLAKE2b(64)
        cases.append(
            (
                Workload(
                    f"legacy_blake2b/{size}/64/cryptography",
                    size,
                    partial(digest.legacy_blake2b, data, width=64),
                    partial(hashes.Hash.hash, algorithm, data),
                ),
                "One-shot API with prepared algorithm; widths other than 64 unsupported",
            )
        )
    for size in (42, 1024, 64 << 10):
        claims = b"x" * size
        bearer = token.mint(_KEY, claims)
        for operation, value in (("mint", claims), ("verify", bearer)):
            # Large synthetic envelopes explicitly bypass the request-size
            # policy so this comparison measures complete verification work.
            envelope_options = (
                {"max_token_chars": None}
                if operation == "verify" and len(bearer) > 8192
                else {}
            )
            cases.append(
                (
                    Workload(
                        f"token.{operation}/{size}/stdlib-base64",
                        size,
                        partial(
                            getattr(token, operation), _KEY, value, **envelope_options
                        ),
                        partial(
                            getattr(stdlib_token, operation),
                            _KEY,
                            value,
                            **envelope_options,
                        ),
                    ),
                    "Complete envelope operation; only base64 provider changes",
                )
            )
        encoded = b64.encode(claims, url=True, pad=False)
        for operation, value in (("encode", claims), ("decode", encoded)):
            options = {"url": True, **({"pad": False} if operation == "encode" else {})}
            cases.append(
                (
                    Workload(
                        f"base64.{operation}/{size}/stdlib",
                        size,
                        partial(getattr(b64, operation), value, **options),
                        partial(getattr(stdlib_token.b64, operation), value, **options),
                    ),
                    "Support codec only; no cryptographic claim for base64",
                )
            )
    return cases


def main() -> None:
    """Record validated experiments and the current per-function baseline."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-path", type=Path)
    parser.add_argument("--blake3-c", type=Path)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--seconds", type=float, default=0.005)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.rounds < 1
        or not math.isfinite(args.seconds)
        or not 0.001 <= args.seconds <= 1
    ):
        parser.error("positive rounds and seconds between .001 and 1 are required")
    if args.candidate_path:
        sys.path.insert(0, str(args.candidate_path.resolve()))
    source = Path(digest.__file__).resolve().parents[2]
    packages: dict[str, str | None] = {}
    for name in ("blake3", "pybase64", "cryptography", "pycryptodome"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    aliases = {
        name: getattr(secure, name) is getattr(secrets, primitive)
        for name, primitive in (
            ("token_bytes", "token_bytes"),
            ("token_hex", "token_hex"),
            ("token_urlsafe", "token_urlsafe"),
            ("below", "randbelow"),
            ("bits", "randbits"),
            ("choice", "choice"),
        )
    }
    if not all(aliases.values()):
        msg = "secure randomness no longer uses exact stdlib aliases"
        raise AssertionError(msg)
    harness_source = Path(__file__).read_bytes()
    report: dict[str, Any] = {
        "schema": 1,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "load_start": _load_average(),
        "ssl_openssl": ssl.OPENSSL_VERSION,
        "packages": packages,
        "source_hashes": _source_hashes(source),
        "harness_sha256": hashlib.sha256(harness_source).hexdigest(),
        "measured_harness_source": harness_source.decode("utf-8"),
        "native_absorb": repr(digest._native_absorb),
        "experimental_c": {
            "path": str(args.blake3_c.resolve()),
            "sha256": hashlib.sha256(args.blake3_c.read_bytes()).hexdigest(),
        }
        if args.blake3_c
        else None,
        "rng_exact_aliases": aliases,
        "ratio_definition": "current time / candidate time; >1 favors candidate",
        "experiments": {},
        "current_function_baseline": {},
    }
    for index, (case, note) in enumerate(_cases(source, args.blake3_c)):
        row = _measure(case, args.rounds, args.seconds, index)
        row["note"] = note
        report["experiments"][case.name] = row
        print(
            f"{case.name:55} {row['median_paired_speedup']:6.2f}x{' noisy' if row['noisy'] else ''}",
            flush=True,
        )
    for baseline in _crypto():
        baseline.check(baseline.run())
        value = measure(baseline.run, rounds=args.rounds)
        report["current_function_baseline"][baseline.name] = {
            "note": baseline.note,
            "minimum_ns": value.per_op_ns,
            "samples_ns": value.samples_ns,
            "spread": value.spread,
        }
    report["load_end"] = _load_average()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
