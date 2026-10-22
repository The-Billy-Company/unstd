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
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
from itertools import product
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import tomllib
from typing import TYPE_CHECKING
import zipfile
import zlib


if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import BinaryIO


# Bound decompression and metadata work even when a compromised build uploads a
# bomb. Hashing member streams does not retain package/native payloads in memory.
MAX_MEMBERS = 100_000
MAX_UNPACKED_BYTES = 2 * 1024**3
MAX_METADATA_BYTES = 8 * 1024**2
CHUNK_BYTES = 1024**2


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


def _digest(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(CHUNK_BYTES):
        size += len(chunk)
        _require(size <= MAX_UNPACKED_BYTES, "archive member exceeds size limit")
        digest.update(chunk)
    return base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii"), size


def _sdist(path: Path) -> tuple[tuple[str, str], dict[str, tuple[str, ...]]]:
    stem = path.name.removesuffix(".tar.gz")
    parts = stem.split("-")
    _require(len(parts) == 2, f"invalid sdist filename: {path.name}")
    filename_identity = _identity(*parts)
    names: dict[str, bool] = {}
    metadata: dict[str, bytes] = {}
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
                    if name in {f"{stem}/PKG-INFO", f"{stem}/pyproject.toml"}:
                        metadata[name] = _read_metadata(stream, member.size)
                    else:
                        _, size = _digest(stream)
                        _require(
                            size == member.size, f"invalid sdist member size: {name!r}"
                        )
    _check_parents(names)
    _require(
        set(metadata) == {f"{stem}/PKG-INFO", f"{stem}/pyproject.toml"},
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
    return identity, dependencies


def _wheel(
    path: Path,
    expected: tuple[str, str],
    expected_dependencies: dict[str, tuple[str, ...]],
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
                    hashes[name] = _digest(stream)
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
        identity, dependencies = _sdist(sdists[0])
        if tag is None:
            tag = f"v{identity[1]}"
        _require(identity[1] == tag[1:], "release tag and artifact version mismatch")
        for path in wheels:
            _wheel(path, identity, dependencies)
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
