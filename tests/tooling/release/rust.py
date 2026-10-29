"""Install pinned Rust using authenticated official Rustup archive executables.

Bootstrap hashes were independently checked against archived checksums and the
downloaded bytes. Rustup verifies the fixed toolchain's component downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from urllib.request import urlopen


if TYPE_CHECKING:
    from collections.abc import Sequence


RUST_VERSION = "1.99.1"
CARGO_VERSION = "1.99.1"  # Verified CLI version; the component manifest uses 0.100.0.
RUSTUP_VERSION = "1.29.2"
DEFAULT_ROOT = Path("/opt/unstd-rust")
# https://static.rust-lang.org/rustup/archive/1.29.2/<target>/rustup-init.sha256
RUSTUP_SHA256 = {
    "x86_64-unknown-linux-gnu": "5be191e8c70a5007074254cb74d78dbfdd25cd5a61c27de7b8e5410122ec24f4",
    "x86_64-unknown-linux-musl": "2ba97d668c06dc4b1884f2a3fa6f4cf14b9bd0c1db379af7e4f87a705ff19edd",
    "aarch64-unknown-linux-gnu": "a9c168dbea81a84671c6b7a5c1795cb0eb699020110d107ddfca427bd797e697",
    "aarch64-unknown-linux-musl": "7fdbb23acf6495efee2e371580c52b365917551b4835090a225c44ac30915235",
}
_SOABI_PROBE = "import sysconfig; print(sysconfig.get_config_var('SOABI'))"
_DOWNLOAD_LIMIT = 64 * 1024 * 1024


def native_target(identifier: str, directory: Path = Path("/opt/python")) -> str | None:
    """Admit the selected native Linux ABI, or skip Python versions before 3.15."""
    digits = identifier.removeprefix("cp")
    if (
        not identifier.startswith("cp3")
        or not digits.isascii()
        or not digits.isdecimal()
        or len(digits) < 3
    ):
        message = "Rust provisioning requires a standard CPython build identifier"
        raise ValueError(message)
    if int(digits[1:]) < 15:
        return None
    machine = platform.machine()
    if sys.platform != "linux" or machine not in {"x86_64", "aarch64"}:
        message = "Rust provisioning requires native Linux x86_64 or aarch64"
        raise RuntimeError(message)
    python = directory / f"{identifier}-{identifier}" / "bin/python"
    soabi = subprocess.check_output(  # noqa: S603 — selected interpreter, fixed probe
        [python, "-I", "-S", "-c", _SOABI_PROBE], text=True, timeout=30
    ).strip()
    prefix = f"cpython-{digits}-{machine}-linux-"
    target = f"{machine}-unknown-linux-{soabi.removeprefix(prefix)}"
    if not soabi.startswith(prefix) or target not in RUSTUP_SHA256:
        message = f"unreviewed native Python ABI for Rust provisioning: {soabi}"
        raise RuntimeError(message)
    return target


def authenticated_installer(target: str, root: Path) -> Path:
    """Authenticate cached or downloaded bootstrap bytes before any execution."""
    expected = RUSTUP_SHA256[target]
    directory = root / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    installer = directory / f"rustup-init-{RUSTUP_VERSION}-{target}"
    if installer.is_file():
        with installer.open("rb") as stream:
            data = stream.read(_DOWNLOAD_LIMIT + 1)
    else:
        url = f"https://static.rust-lang.org/rustup/archive/{RUSTUP_VERSION}/{target}/rustup-init"
        with urlopen(url, timeout=60) as response:
            if urlsplit(response.geturl()).scheme != "https":
                message = "Rustup download redirected away from HTTPS"
                raise RuntimeError(message)
            data = response.read(_DOWNLOAD_LIMIT + 1)
    if len(data) > _DOWNLOAD_LIMIT or hashlib.sha256(data).hexdigest() != expected:
        message = f"Rustup checksum or size mismatch for {target}"
        raise RuntimeError(message)
    if not installer.is_file():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        try:
            temporary.chmod(0o755)
            temporary.replace(installer)
        finally:
            temporary.unlink(missing_ok=True)
    installer.chmod(0o755)
    return installer


def compiler_environment(root: Path) -> dict[str, str]:
    """Isolate Rust state and use only the official HTTPS component service."""
    environment = dict(os.environ)
    environment.pop("RUSTUP_TOOLCHAIN", None)
    environment.update(
        CARGO_HOME=str(root / "cargo"),
        RUSTUP_HOME=str(root / "rustup"),
        RUSTUP_DIST_SERVER="https://static.rust-lang.org",
        RUSTUP_UPDATE_ROOT="https://static.rust-lang.org/rustup",
    )
    environment["PATH"] = (
        str(root / "cargo/bin") + os.pathsep + environment.get("PATH", "")
    )
    return environment


def provision(identifier: str, root: Path = DEFAULT_ROOT) -> dict[str, object]:
    """Install and validate the pinned compiler without a global Cargo fallback."""
    target = native_target(identifier)
    if target is None:
        return {
            "identifier": identifier,
            "skipped": "Rust is required from CPython 3.15",
        }
    if not root.is_absolute():
        message = "Rust provisioning requires an absolute installation root"
        raise ValueError(message)
    installer = authenticated_installer(target, root)
    environment = compiler_environment(root)
    subprocess.run(  # noqa: S603 — authenticated official executable, fixed arguments
        [
            installer,
            "-y",
            "--no-modify-path",
            "--profile",
            "minimal",
            "--default-host",
            target,
            "--default-toolchain",
            RUST_VERSION,
        ],
        check=True,
        env=environment,
        timeout=300,
    )
    versions = {}
    for tool, expected in (
        ("rustup", RUSTUP_VERSION),
        ("rustc", RUST_VERSION),
        ("cargo", CARGO_VERSION),
    ):
        output = subprocess.check_output(  # noqa: S603 — provisioned tool, fixed probe
            [root / "cargo/bin" / tool, "--version"],
            text=True,
            env=environment,
            timeout=30,
        ).strip()
        if output.split()[:2] != [tool, expected]:
            message = f"provisioned {tool} does not match the pinned version: {output}"
            raise RuntimeError(message)
        versions[tool] = output
    return {
        "identifier": identifier,
        "target": target,
        "rustup_sha256": RUSTUP_SHA256[target],
        "versions": versions,
        "cargo_home": str(root / "cargo"),
        "rustup_home": str(root / "rustup"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Provision the selected cibuildwheel interpreter before dependency builds."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identifier")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    print(json.dumps(provision(args.identifier, args.root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
