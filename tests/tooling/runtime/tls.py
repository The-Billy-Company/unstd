"""Admit the actual stdlib TLS engine against reviewed upstream security fixes.

This is provisioning/release tooling, never imported by ``unstd.crypto.tls``.
Version comparisons are per OpenSSL release branch: 3.6.0 is newer than 3.5.9
numerically but still lacks the September 2026 certificate-processing fixes.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import ssl
import sys
from typing import TYPE_CHECKING, cast


if TYPE_CHECKING:
    from collections.abc import Sequence


POLICY_REVIEWED = "2026-10-22"
ADVISORY = "https://openssl-library.org/news/secadv/20260929.txt"
# CVE-2026-35189 applies even when CRL checking is disabled. The same release
# set repairs the other applicable September advisories. OpenSSL 3.0.23 is a
# premium-support release; an older distributor version needs separate evidence
# of backports and is not automatically admitted by a generic version check.
_PATCH_FLOORS = {(3, 0): 23, (3, 4): 8, (3, 5): 9, (3, 6): 5, (4, 0): 3}


def openssl_policy_errors(
    version: str,
    version_info: tuple[int, int, int, int, int],
    *,
    has_tls_1_3: bool,
) -> tuple[str, ...]:
    """Diagnose an upstream engine without trusting numeric cross-branch order.

    Unknown engines/branches, prereleases and mismatched runtime metadata require
    an explicit policy review. Distributor backports cannot be inferred from an
    upstream version string; this tool deliberately has no silent bypass flag.
    """
    errors: list[str] = []
    if not has_tls_1_3:
        errors.append("the interpreter must support TLS 1.3")
    match = re.match(r"^OpenSSL (\d+)\.(\d+)\.(\d+)(?: |$)", version)
    if match is None:
        errors.append("unreviewed TLS engine or prerelease; review runtime policy")
        return tuple(errors)
    major, minor, patch = (int(part) for part in match.groups())
    # Since OpenSSL 3, CPython retains the historical five-field encoding:
    # 3.5.9 is (3, 5, 0, 9, 0), not (3, 5, 9, ...).
    if version_info != (major, minor, 0, patch, 0):
        errors.append("OpenSSL version text and runtime version fields disagree")
        return tuple(errors)
    floor = _PATCH_FLOORS.get((major, minor))
    if floor is None:
        errors.append(
            f"unreviewed or unsupported OpenSSL branch {major}.{minor}; "
            "review runtime policy against current upstream advisories"
        )
    elif patch < floor:
        errors.append(
            f"OpenSSL {major}.{minor}.{patch} lacks reviewed security fixes; "
            f"upgrade the interpreter's linked OpenSSL to {major}.{minor}.{floor} "
            f"or newer in this branch ({ADVISORY})"
        )
    return tuple(errors)


def runtime_report() -> dict[str, object]:
    """Inspect this process's stdlib engine, not a separately bundled dependency."""
    errors = openssl_policy_errors(
        ssl.OPENSSL_VERSION,
        ssl.OPENSSL_VERSION_INFO,
        has_tls_1_3=ssl.HAS_TLSv1_3,
    )
    return {
        "schema": 1,
        "policy_reviewed": POLICY_REVIEWED,
        "advisory": ADVISORY,
        "python": platform.python_version(),
        "executable": sys.executable,
        "openssl": ssl.OPENSSL_VERSION,
        "openssl_version_info": list(ssl.OPENSSL_VERSION_INFO),
        "tls_1_3": ssl.HAS_TLSv1_3,
        "errors": list(errors),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Print reviewable runtime evidence and fail before build/test/deployment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit structured evidence")
    args = parser.parse_args(argv)
    report = runtime_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Python {report['python']}: {report['openssl']}")
        print(f"Executable: {report['executable']}")
        print(f"TLS security policy reviewed {POLICY_REVIEWED}")
        for error in cast("list[str]", report["errors"]):
            print(f"TLS runtime rejected: {error}", file=sys.stderr)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
