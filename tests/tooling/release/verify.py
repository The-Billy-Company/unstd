"""Verify a complete release without installing or executing its contents.

Only the standard library is imported, so the publication trust boundary does
not depend on the project or on packages obtained from the package index. The
report records the exact artifact bytes that passed the checks; keep it outside
the directory handed to a publisher.
"""

from __future__ import annotations

import argparse
import base64
import csv
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
from itertools import product
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import tarfile
import tomllib
from typing import TYPE_CHECKING
import zipfile
import zlib


if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import IO, BinaryIO


# Bound decompression and metadata work even when a compromised build uploads a
# bomb. Hashing member streams does not retain package/native payloads in memory.
MAX_MEMBERS = 100_000
MAX_UNPACKED_BYTES = 2 * 1024**3
MAX_METADATA_BYTES = 8 * 1024**2
CHUNK_BYTES = 1024**2


@dataclass(frozen=True)
class _Vendor:
    project: str
    version: str
    license: str
    license_file: str
    archive_sha256: str
    license_sha256: str
    archive_url: str
    provenance_sha256: str

    @property
    def root(self) -> str:
        return f"native/compress/vendor/{self.project}"


_VENDORS = (
    _Vendor(
        "libdeflate",
        "1.25",
        "MIT",
        "COPYING",
        "fed5cd22f00f30cc4c2e5329f94e2b8a901df9fa45ee255cb70e2b0b42344477",
        "4ad69099cb4374836fd27583d9991e2838cd86b6e4666ab26b2d32582c91e73a",
        "https://github.com/ebiggers/libdeflate/releases/download/v1.25/libdeflate-1.25.tar.gz",
        "3471af922c51d680593fe7665590020fab9678dee751a4c3b494bfb8f8f1c54e",
    ),
    _Vendor(
        "zlib",
        "1.3.2",
        "Zlib",
        "LICENSE",
        "bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16",
        "e32ff4e00d9d94930537635291da39e7e612703334bf6fde8c7f1686fe8a45a2",
        "https://zlib.net/zlib-1.3.2.tar.gz",
        "d82745c9e55db5936464cb2f7d353d949a47600e3b308597d98993484e1aa77c",
    ),
)
_NATIVE_MODULES = (
    "unstd/serde/_json_native",
    "unstd/clone/_clone_native",
    "unstd/crypto/_stream_native",
    "unstd/text/_text_native",
    "unstd/compress/_gzip_native",
    "unstd/rand/_rand_native",
    "unstd/fs/_fs_native",
)
_MACHO_ORDERS = {
    b"\xcf\xfa\xed\xfe": "<",  # 64-bit little endian
    b"\xfe\xed\xfa\xcf": ">",  # 64-bit big endian
    b"\xce\xfa\xed\xfe": None,  # 32-bit Mach-O
    b"\xfe\xed\xfa\xce": None,
    b"\xca\xfe\xba\xbe": None,  # Fat Mach-O
    b"\xbe\xba\xfe\xca": None,
    b"\xca\xfe\xba\xbf": None,  # 64-bit fat Mach-O
    b"\xbf\xba\xfe\xca": None,
}


class ReleaseError(ValueError):
    """An artifact set is unsafe, incomplete, inconsistent, or unauthenticated."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def _identity(name: object, version: object) -> tuple[str, str]:
    _require(
        isinstance(name, str)
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name)
        is not None,
        "missing or invalid package Name",
    )
    _require(
        isinstance(version, str)
        and re.fullmatch(r"[0-9][A-Za-z0-9.!+]*", version) is not None,
        "missing or invalid package Version",
    )
    assert isinstance(name, str)
    assert isinstance(version, str)
    return re.sub(r"[-_.]+", "-", name).lower(), version


def _metadata(data: bytes) -> tuple[str, str]:
    message = BytesParser(policy=default).parsebytes(data)
    _require(
        len(message.get_all("Name", [])) == len(message.get_all("Version", [])) == 1,
        "metadata must contain exactly one Name and Version",
    )
    return _identity(message["Name"], message["Version"])


def _dependency_metadata(data: bytes) -> dict[str, tuple[str, ...]]:
    message = BytesParser(policy=default).parsebytes(data)
    fields = {}
    for field in (
        "Requires-Python",
        "Requires-Dist",
        "Provides-Extra",
        "License-Expression",
        "License-File",
    ):
        values = message.get_all(field, [])
        if field in {"Requires-Python", "License-Expression"}:
            _require(len(values) <= 1, f"duplicate metadata field: {field}")
        fields[field] = tuple(sorted(str(value) for value in values))
    return fields


def _source_dependencies(
    project: dict[str, object], metadata: dict[str, tuple[str, ...]]
) -> None:
    # unstd promises a zero-dependency base install. Derive the full allowed
    # extra closure from the static source manifest, so forged dependencies in
    # both archive formats cannot launder one another's metadata.
    _require(
        project.get("dependencies", []) == [],
        "source project must have no base dependencies",
    )
    extras = project.get("optional-dependencies", {})
    _require(isinstance(extras, dict), "source optional-dependencies must be a table")
    assert isinstance(extras, dict)
    expected_extras = []
    expected_requirements = []
    for extra, requirements in extras.items():
        name, _ = _identity(extra, "0")
        expected_extras.append(name)
        _require(
            isinstance(requirements, list),
            f"source extra {extra!r} must contain a dependency list",
        )
        for requirement in requirements:
            _require(
                isinstance(requirement, str)
                and bool(requirement)
                and not any(
                    token in requirement for token in (";", "@", "://", "\n", "\r")
                ),
                f"source extra {extra!r} must use simple registry requirements",
            )
            expected_requirements.append(f"{requirement}; extra == '{name}'")
    _require(
        metadata["Provides-Extra"] == tuple(sorted(expected_extras)),
        "sdist metadata extras differ from source project",
    )
    _require(
        metadata["Requires-Dist"] == tuple(sorted(expected_requirements)),
        "sdist metadata dependencies differ from source project",
    )


def _member_path(name: str, *, directory: bool) -> str:
    path = name.removesuffix("/") if directory else name
    _require(
        bool(path)
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
        and "\\" not in name
        and ":" not in name
        and not path.startswith("/")
        and all(part not in {"", ".", ".."} for part in path.split("/")),
        f"unsafe archive path: {name!r}",
    )
    return path


def _add_member(names: dict[str, bool], name: str, *, directory: bool) -> str:
    path = _member_path(name, directory=directory)
    _require(path not in names, f"duplicate archive member: {name!r}")
    names[path] = directory
    _require(len(names) <= MAX_MEMBERS, "archive contains too many members")
    return path


def _check_parents(names: dict[str, bool]) -> None:
    for name in names:
        for parent in PurePosixPath(name).parents:
            _require(
                names.get(str(parent), True),
                f"archive file shadows directory: {parent}",
            )


def _read_metadata(stream: BinaryIO, size: int) -> bytes:
    _require(size <= MAX_METADATA_BYTES, "archive metadata exceeds size limit")
    data = stream.read(MAX_METADATA_BYTES + 1)
    _require(len(data) == size, "archive metadata has invalid size")
    return data


def _digest(stream: BinaryIO, prefix: bytes = b"") -> tuple[str, int]:
    digest = hashlib.sha256(prefix)
    size = len(prefix)
    while chunk := stream.read(CHUNK_BYTES):
        size += len(chunk)
        _require(size <= MAX_UNPACKED_BYTES, "archive member exceeds size limit")
        digest.update(chunk)
    return base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii"), size


def _macos_floors(platforms: str) -> dict[int, tuple[int, int, int]]:
    floors: dict[int, tuple[int, int, int]] = {}
    for platform in platforms.split("."):
        platform = platform.lower()
        if not platform.startswith("macosx_"):
            continue
        match = re.fullmatch(r"macosx_(\d+)_(\d+)_(x86_64|arm64)", platform)
        _require(match is not None, f"unsupported macOS wheel platform: {platform}")
        assert match is not None
        major, minor = int(match[1]), int(match[2])
        _require(0 < major < 65536 and minor < 256, "invalid macOS deployment tag")
        machine = 0x01000007 if match[3] == "x86_64" else 0x0100000C
        floor = major, minor, 0
        floors[machine] = min(floor, floors.get(machine, floor))
    return floors


def _macos_payload(
    stream: IO[bytes],
    size: int,
    name: str,
    floors: dict[int, tuple[int, int, int]],
) -> bytes:
    # Inspect only the bounded header/load-command prefix; hash the body once.
    prefix = stream.read(4)
    if prefix not in _MACHO_ORDERS and not name.lower().endswith(
        (".so", ".dylib", ".bundle", ".pyd")
    ):
        return prefix
    order = _MACHO_ORDERS.get(prefix)
    _require(order is not None, f"unsupported or malformed Mach-O payload: {name}")
    assert order is not None
    header = stream.read(28)
    _require(len(header) == 28, f"truncated Mach-O header: {name}")
    machine, _, _, count, command_bytes, _, _ = struct.unpack(order + "7I", header)
    _require(machine in floors, f"Mach-O architecture differs from wheel tags: {name}")
    _require(
        command_bytes <= MAX_METADATA_BYTES
        and 32 + command_bytes <= size
        and count <= command_bytes // 8,
        f"invalid or oversized Mach-O load-command table: {name}",
    )
    commands = stream.read(command_bytes)
    _require(len(commands) == command_bytes, f"truncated Mach-O load commands: {name}")
    offset = 0
    minimum: tuple[int, int, int] | None = None
    for _ in range(count):
        _require(offset + 8 <= command_bytes, f"truncated Mach-O load command: {name}")
        command, length = struct.unpack_from(order + "2I", commands, offset)
        _require(
            length >= 8 and length % 8 == 0 and offset + length <= command_bytes,
            f"invalid Mach-O load-command size: {name}",
        )
        value = None
        if command == 0x24:  # LC_VERSION_MIN_MACOSX
            _require(length == 16, f"invalid Mach-O deployment command: {name}")
            value = struct.unpack_from(order + "I", commands, offset + 8)[0]
        elif command == 0x32:  # LC_BUILD_VERSION
            _require(length >= 24, f"invalid Mach-O build-version command: {name}")
            platform, value, _, tools = struct.unpack_from(
                order + "4I", commands, offset + 8
            )
            _require(
                platform == 1 and length == 24 + tools * 8,
                f"invalid Mach-O macOS build-version command: {name}",
            )
        if value is not None:
            _require(
                minimum is None and value >> 16 > 0,
                f"ambiguous Mach-O deployment version: {name}",
            )
            minimum = value >> 16, value >> 8 & 255, value & 255
        offset += length
    _require(offset == command_bytes, f"inconsistent Mach-O load-command table: {name}")
    _require(minimum is not None, f"Mach-O has no macOS deployment command: {name}")
    assert minimum is not None
    _require(
        minimum <= floors[machine],
        f"Mach-O deployment version {minimum} exceeds wheel platform {floors[machine]}: {name}",
    )
    return prefix + header + commands


def _vendor_license(
    stem: str,
    metadata: dict[str, bytes],
    hashes: dict[str, tuple[str, int]],
    vendor: _Vendor,
) -> tuple[str, str] | None:
    root = f"{stem}/{vendor.root}/"
    context = f"vendored {vendor.project}"
    files = {name.removeprefix(root) for name in hashes if name.startswith(root)}
    if not files:
        return None
    provenance = root + "UPSTREAM.json"
    _require(provenance in metadata, f"{context} provenance is missing")
    _require(
        hashlib.sha256(metadata[provenance]).hexdigest() == vendor.provenance_sha256,
        f"{context} provenance differs from the reviewed source selection",
    )
    upstream = json.loads(metadata[provenance])
    _require(
        isinstance(upstream, dict)
        and upstream.get("project") == vendor.project
        and upstream.get("version") == vendor.version
        and upstream.get("license") == vendor.license
        and upstream.get("archive_sha256") == vendor.archive_sha256
        and upstream.get("archive_url") == vendor.archive_url,
        f"{context} provenance differs from the reviewed release",
    )
    declared = upstream.get("files")
    _require(
        isinstance(declared, dict)
        and declared.get(vendor.license_file) == vendor.license_sha256,
        f"{context} license differs from the reviewed license",
    )
    assert isinstance(declared, dict)
    _require(
        files == declared.keys() | {"UPSTREAM.json"},
        f"{context} file inventory differs from provenance",
    )
    for name, digest in declared.items():
        _member_path(name, directory=False)
        _require(
            isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            f"{context} provenance has an invalid source hash",
        )
        encoded = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=").decode()
        _require(
            hashes[root + name][0] == encoded,
            f"{context} source hash mismatch: {name!r}",
        )
    return f"{vendor.root}/{vendor.license_file}", hashes[root + vendor.license_file][0]


def _sdist(
    path: Path,
) -> tuple[tuple[str, str], dict[str, tuple[str, ...]], tuple[tuple[str, str], ...]]:
    stem = path.name.removesuffix(".tar.gz")
    parts = stem.split("-")
    _require(len(parts) == 2, f"invalid sdist filename: {path.name}")
    filename_identity = _identity(*parts)
    names: dict[str, bool] = {}
    metadata: dict[str, bytes] = {}
    hashes: dict[str, tuple[str, int]] = {}
    unpacked = 0
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive:
            _require(
                member.isdir() or member.isfile(),
                f"non-regular sdist member: {member.name!r}",
            )
            name = _add_member(names, member.name, directory=member.isdir())
            _require(
                name == stem or name.startswith(stem + "/"),
                "sdist has an unexpected archive root",
            )
            _require(
                name != stem or member.isdir(), "sdist archive root must be a directory"
            )
            unpacked += member.size
            _require(
                unpacked <= MAX_UNPACKED_BYTES, "sdist exceeds unpacked size limit"
            )
            if member.isfile():
                stream = archive.extractfile(member)
                _require(stream is not None, f"unreadable sdist member: {name!r}")
                assert stream is not None
                with stream:
                    if name in {
                        f"{stem}/PKG-INFO",
                        f"{stem}/pyproject.toml",
                        *(f"{stem}/{vendor.root}/UPSTREAM.json" for vendor in _VENDORS),
                    }:
                        metadata[name] = _read_metadata(stream, member.size)
                        hashes[name] = _digest(io.BytesIO(metadata[name]))
                    else:
                        hashes[name] = _digest(stream)
                        _, size = hashes[name]
                        _require(
                            size == member.size, f"invalid sdist member size: {name!r}"
                        )
    _check_parents(names)
    _require(
        {f"{stem}/PKG-INFO", f"{stem}/pyproject.toml"} <= metadata.keys(),
        "sdist must contain root PKG-INFO and pyproject.toml",
    )
    identity = _metadata(metadata[f"{stem}/PKG-INFO"])
    project = tomllib.loads(metadata[f"{stem}/pyproject.toml"].decode("utf-8")).get(
        "project"
    )
    _require(isinstance(project, dict), "sdist pyproject.toml has no project table")
    assert isinstance(project, dict)
    _require(
        _identity(project.get("name"), project.get("version")) == identity,
        "sdist pyproject and PKG-INFO identity mismatch",
    )
    _require(
        filename_identity == identity, "sdist filename and metadata identity mismatch"
    )
    dependencies = _dependency_metadata(metadata[f"{stem}/PKG-INFO"])
    _source_dependencies(project, dependencies)
    licenses = tuple(
        license_file
        for vendor in _VENDORS
        if (license_file := _vendor_license(stem, metadata, hashes, vendor)) is not None
    )
    if licenses:
        _require(
            len(licenses) == len(_VENDORS),
            "sdist omits a reviewed native gzip source dependency",
        )
        expression = "Apache-2.0 AND MIT AND Zlib"
        _require(
            project.get("license") == expression
            and dependencies["License-Expression"] == (expression,),
            "sdist license expression must include both vendored gzip dependencies",
        )
    for license_file, _ in licenses:
        _require(
            license_file in dependencies["License-File"],
            f"sdist metadata omits the vendored license: {license_file}",
        )
    return identity, dependencies, licenses


def _wheel(
    path: Path,
    expected: tuple[str, str],
    expected_dependencies: dict[str, tuple[str, ...]],
    vendor_licenses: tuple[tuple[str, str], ...],
) -> None:
    parts = path.name.removesuffix(".whl").split("-")
    _require(len(parts) in {5, 6}, f"invalid wheel filename: {path.name}")
    _require(
        _identity(*parts[:2]) == expected,
        "wheel filename and release identity mismatch",
    )
    if len(parts) == 6:
        _require(
            re.fullmatch(r"[0-9][A-Za-z0-9_]*", parts[2]) is not None,
            "invalid wheel build tag",
        )
    _require(
        all(
            re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", part) is not None
            for part in parts[-3:]
        ),
        "invalid wheel compatibility tag",
    )
    tags = {
        "-".join(values)
        for values in product(*(part.split(".") for part in parts[-3:]))
    }
    macos_floors = _macos_floors(parts[-1])
    root = f"{parts[0]}-{parts[1]}.dist-info"
    record = f"{root}/RECORD"
    names: dict[str, bool] = {}
    hashes: dict[str, tuple[str, int]] = {}
    metadata: dict[str, bytes] = {}
    unpacked = 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            _require(
                member.orig_filename == member.filename,
                "wheel contains a truncated archive path",
            )
            name = _add_member(names, member.filename, directory=member.is_dir())
            mode = stat.S_IFMT(member.external_attr >> 16)
            _require(
                mode in {0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG},
                f"non-regular wheel member: {name!r}",
            )
            _require(not member.flag_bits & 1, "wheel contains encrypted files")
            unpacked += member.file_size
            _require(
                unpacked <= MAX_UNPACKED_BYTES, "wheel exceeds unpacked size limit"
            )
            first = name.split("/")[0]
            if first.lower().endswith(".dist-info"):
                _require(first == root, "wheel contains unexpected dist-info directory")
            if member.is_dir():
                continue
            with archive.open(member) as stream:
                if name in {record, f"{root}/METADATA", f"{root}/WHEEL"}:
                    data = _read_metadata(stream, member.file_size)
                    metadata[name] = data
                    hashes[name] = _digest(io.BytesIO(data))
                else:
                    prefix = (
                        _macos_payload(stream, member.file_size, name, macos_floors)
                        if macos_floors
                        else b""
                    )
                    hashes[name] = _digest(stream, prefix)
            _require(
                hashes[name][1] == member.file_size,
                f"invalid wheel member size: {name!r}",
            )
    _check_parents(names)
    _require(
        set(metadata) == {record, f"{root}/METADATA", f"{root}/WHEEL"},
        "wheel must contain METADATA, WHEEL and RECORD",
    )
    _require(
        _metadata(metadata[f"{root}/METADATA"]) == expected,
        "wheel metadata and release identity mismatch",
    )
    _require(
        _dependency_metadata(metadata[f"{root}/METADATA"]) == expected_dependencies,
        "wheel dependency metadata differs from sdist PKG-INFO",
    )
    _require(
        bool(vendor_licenses)
        or not any(name.startswith("unstd/compress/_gzip_native.") for name in hashes),
        "native gzip wheel has no authenticated vendored license",
    )
    for license_name, digest in vendor_licenses:
        _require(
            hashes.get(f"{root}/licenses/{license_name}", (None,))[0] == digest,
            f"wheel omits or changes the vendored license: {license_name}",
        )
    linux = all(
        platform.startswith(("linux_", "manylinux", "musllinux"))
        for platform in parts[-1].lower().split(".")
    )
    if not linux:
        _require(
            not any(
                re.fullmatch(
                    r"unstd/fs/_fs_native(?:\.[a-zA-Z0-9_-]+)?\.(?:so|pyd)",
                    name,
                )
                for name in hashes
            ),
            "non-Linux wheel contains the Linux-only filesystem accelerator",
        )
    if vendor_licenses and parts[-1] != "any":
        for module in _NATIVE_MODULES:
            if module == "unstd/fs/_fs_native" and not linux:
                continue
            matches = [
                name
                for name in hashes
                if re.fullmatch(
                    re.escape(module) + r"(?:\.[a-zA-Z0-9_-]+)?\.(?:so|pyd)",
                    name,
                )
            ]
            _require(
                len(matches) == 1,
                f"native wheel must contain exactly one accelerator: {module}",
            )
    wheel_metadata = BytesParser(policy=default).parsebytes(metadata[f"{root}/WHEEL"])
    _require(
        wheel_metadata.get_all("Wheel-Version") == ["1.0"],
        "unsupported or duplicate Wheel-Version",
    )
    _require(
        wheel_metadata.get_all("Root-Is-Purelib") in (["true"], ["false"]),
        "invalid Root-Is-Purelib",
    )
    wheel_tags = wheel_metadata.get_all("Tag", [])
    _require(
        len(wheel_tags) == len(tags) and set(wheel_tags) == tags,
        "wheel filename and WHEEL compatibility tags mismatch",
    )
    _require(
        wheel_metadata.get_all("Build", []) == (parts[2:3] if len(parts) == 6 else []),
        "wheel filename and WHEEL build tag mismatch",
    )
    rows: dict[str, tuple[str, str]] = {}
    for entry in csv.reader(io.StringIO(metadata[record].decode("utf-8")), strict=True):
        _require(len(entry) == 3, "RECORD row must contain three fields")
        name, digest, size = entry
        _member_path(name, directory=False)
        _require(name not in rows, f"duplicate RECORD member: {name!r}")
        rows[name] = digest, size
    _require(
        rows.keys() == hashes.keys(), "RECORD must list every wheel file exactly once"
    )
    _require(rows[record] == ("", ""), "RECORD must not hash itself")
    for name, (digest, size) in hashes.items():
        if name != record:
            _require(
                rows[name] == (f"sha256={digest}", str(size)),
                f"RECORD hash or size mismatch: {name!r}",
            )


def verify(dist: Path, tag: str | None = None) -> dict[str, object]:
    """Check a publication set and return deterministic hashes of its bytes."""
    if tag is not None:
        _require(tag.startswith("v"), "release tag must start with v")
        _identity("release", tag[1:])
    _require(
        dist.is_dir() and not dist.is_symlink(), "dist must be a regular directory"
    )
    files = sorted(dist.iterdir(), key=lambda path: path.name)
    _require(bool(files), "dist is empty")
    for path in files:
        _require(
            path.is_file() and not path.is_symlink(),
            f"unexpected dist entry: {path.name!r}",
        )
        _require(
            path.name.endswith((".tar.gz", ".whl")),
            f"unexpected dist file: {path.name!r}",
        )
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    wheels = [path for path in files if path.suffix == ".whl"]
    _require(
        len(sdists) == 1 and bool(wheels),
        "release requires exactly one sdist and at least one wheel",
    )
    try:
        identity, dependencies, vendor_licenses = _sdist(sdists[0])
        if tag is None:
            tag = f"v{identity[1]}"
        _require(identity[1] == tag[1:], "release tag and artifact version mismatch")
        for path in wheels:
            _wheel(path, identity, dependencies, vendor_licenses)
    except (
        OSError,
        EOFError,
        KeyError,
        NotImplementedError,
        RuntimeError,
        UnicodeError,
        tarfile.TarError,
        zipfile.BadZipFile,
        zlib.error,
        csv.Error,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
    ) as exc:
        message = f"invalid release archive: {exc}"
        raise ReleaseError(message) from exc
    artifacts = []
    for path in files:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        artifacts.append(
            {"filename": path.name, "size": path.stat().st_size, "sha256": digest}
        )
    return {
        "schema_version": 1,
        "name": identity[0],
        "version": identity[1],
        "tag": tag,
        "artifacts": artifacts,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Verify release archives and optionally save their authenticated inventory."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("verify", help="verify the exact files to publish")
    command.add_argument("--dist", type=Path, required=True)
    command.add_argument(
        "--tag", help="expected v-prefixed version; inferred from sdist when omitted"
    )
    command.add_argument("--manifest", type=Path)
    command.add_argument(
        "--expected-manifest",
        type=Path,
        help="require the exact previously verified inventory",
    )
    args = parser.parse_args(argv)
    try:
        if args.manifest is not None:
            _require(
                not args.manifest.resolve().is_relative_to(args.dist.resolve()),
                "manifest must be outside dist",
            )
        report = verify(args.dist, args.tag)
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.expected_manifest is not None:
            _require(
                not args.expected_manifest.resolve().is_relative_to(
                    args.dist.resolve()
                ),
                "expected manifest must be outside dist",
            )
            _require(
                args.expected_manifest.stat().st_size <= MAX_METADATA_BYTES,
                "expected manifest exceeds size limit",
            )
            expected = json.loads(args.expected_manifest.read_text(encoding="utf-8"))
            _require(
                json.dumps(expected, sort_keys=True)
                == json.dumps(report, sort_keys=True),
                "artifacts do not match expected manifest",
            )
        if args.manifest is not None:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
    except (ReleaseError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"release verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
