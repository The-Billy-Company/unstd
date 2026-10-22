"""Hostile release sets must fail before any artifact reaches a publisher."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import shutil
import stat
import subprocess
import sys
import tarfile
from typing import TYPE_CHECKING
import warnings
import zipfile

import pytest

from tests.tooling import release


if TYPE_CHECKING:
    from pathlib import Path


VERSION = "3.1.0"
WHEEL = "unstd-3.1.0-py3-none-any.whl"
SDIST = "unstd-3.1.0.tar.gz"
INFO = "unstd-3.1.0.dist-info"
RECORD = f"{INFO}/RECORD"
METADATA = b"Metadata-Version: 2.4\nName: unstd\nVersion: 3.1.0\n\n"


def _write_sdist(
    path: Path, members: list[tuple[str, bytes | tarfile.TarInfo]] | None = None
) -> None:
    if members is None:
        members = [
            ("unstd-3.1.0/PKG-INFO", METADATA),
            (
                "unstd-3.1.0/pyproject.toml",
                b'[project]\nname = "unstd"\nversion = "3.1.0"\n',
            ),
            ("unstd-3.1.0/src/unstd/__init__.py", b"pass\n"),
        ]
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members:
            member = tarfile.TarInfo(name)
            if isinstance(content, tarfile.TarInfo):
                archive.addfile(content)
            else:
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))


def _wheel_files() -> dict[str, bytes]:
    return {
        "unstd/__init__.py": b"pass\n",
        f"{INFO}/METADATA": METADATA,
        f"{INFO}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
    }


def _record(files: dict[str, bytes]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    for name, content in files.items():
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(content).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        writer.writerow((name, f"sha256={digest}", str(len(content))))
    writer.writerow((RECORD, "", ""))
    return text.getvalue().encode()


def _write_wheel(path: Path, files: dict[str, bytes] | None = None) -> None:
    contents = _wheel_files() if files is None else files.copy()
    if RECORD not in contents:
        contents[RECORD] = _record(contents)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in contents.items():
            archive.writestr(name, content)


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    path = tmp_path / "dist"
    path.mkdir()
    _write_sdist(path / SDIST)
    _write_wheel(path / WHEEL)
    return path


def test_manifest_hashes_exact_bytes_with_stable_names_and_order(
    dist: Path, tmp_path: Path
) -> None:
    report = release.verify(dist, "v3.1.0")
    assert report == {
        "schema_version": 1,
        "name": "unstd",
        "version": VERSION,
        "tag": "v3.1.0",
        "artifacts": [
            {
                "filename": path.name,
                "size": len(path.read_bytes()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(dist.iterdir())
        ],
    }
    copied = tmp_path / "elsewhere"
    shutil.copytree(dist, copied)
    assert release.verify(copied) == report


def test_multiple_wheels_and_build_tags_are_verified(dist: Path) -> None:
    files = _wheel_files()
    files[f"{INFO}/WHEEL"] = (
        b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp313-cp313-manylinux_2_17_x86_64\nBuild: 1\n\n"
    )
    _write_wheel(dist / "unstd-3.1.0-1-cp313-cp313-manylinux_2_17_x86_64.whl", files)
    assert len(release.verify(dist)["artifacts"]) == 3


@pytest.mark.parametrize(
    "filename",
    ["notes.txt", "manifest.json", "unstd.zip", ".hidden", "unstd-3.1.0.tar.gz.asc"],
)
def test_unexpected_publication_files_fail(dist: Path, filename: str) -> None:
    (dist / filename).write_bytes(b"not a distribution")
    with pytest.raises(release.ReleaseError, match="unexpected dist file"):
        release.verify(dist)


def test_directories_and_symlinked_artifacts_fail(dist: Path) -> None:
    (dist / "extra").mkdir()
    with pytest.raises(release.ReleaseError, match="unexpected dist entry"):
        release.verify(dist)
    (dist / "extra").rmdir()
    (dist / "linked.whl").symlink_to(dist / WHEEL)
    with pytest.raises(release.ReleaseError, match="unexpected dist entry"):
        release.verify(dist)


@pytest.mark.parametrize("missing", [WHEEL, SDIST])
def test_both_build_formats_are_required(dist: Path, missing: str) -> None:
    (dist / missing).unlink()
    with pytest.raises(
        release.ReleaseError, match="exactly one sdist and at least one wheel"
    ):
        release.verify(dist)


def test_multiple_sdists_fail(dist: Path) -> None:
    shutil.copyfile(dist / SDIST, dist / "unstd-3.1.1.tar.gz")
    with pytest.raises(release.ReleaseError, match="exactly one sdist"):
        release.verify(dist)


@pytest.mark.parametrize("tag", ["3.1.0", "v3.1.1", "v../3.1.0", "v"])
def test_tag_must_match_exact_artifact_version(dist: Path, tag: str) -> None:
    with pytest.raises(release.ReleaseError):
        release.verify(dist, tag)


@pytest.mark.parametrize(
    "filename",
    [
        "other-3.1.0-py3-none-any.whl",
        "unstd-3.1.1-py3-none-any.whl",
        "unstd-3.1.0-bad.whl",
    ],
)
def test_wheel_filename_cannot_impersonate_another_release(
    dist: Path, filename: str
) -> None:
    (dist / WHEEL).rename(dist / filename)
    with pytest.raises(release.ReleaseError, match="wheel filename"):
        release.verify(dist)


@pytest.mark.parametrize(
    "data",
    [
        b"Name: other\nVersion: 3.1.0\n",
        b"Name: unstd\nVersion: 3.1.1\n",
        b"Name: unstd\nName: second\nVersion: 3.1.0\n\n",
        b"Name: unstd\n",
    ],
)
def test_wheel_metadata_identity_is_authenticated(dist: Path, data: bytes) -> None:
    files = _wheel_files()
    files[f"{INFO}/METADATA"] = data
    _write_wheel(dist / WHEEL, files)
    with pytest.raises(release.ReleaseError, match=r"metadata|Version"):
        release.verify(dist)


def test_sdist_pyproject_must_match_pkg_info(dist: Path) -> None:
    _write_sdist(
        dist / SDIST,
        [
            ("unstd-3.1.0/PKG-INFO", METADATA),
            ("unstd-3.1.0/pyproject.toml", b'[project]\nname="other"\nversion="3.1.0"'),
        ],
    )
    with pytest.raises(release.ReleaseError, match="pyproject and PKG-INFO"):
        release.verify(dist)


@pytest.mark.parametrize(
    "field",
    [
        b"Requires-Python: >=3.13\n",
        b"Requires-Dist: surprise-package>=1\n",
        b"Provides-Extra: injected\n",
        b"License-Expression: MIT\n",
    ],
)
def test_rehashed_wheel_cannot_change_dependency_policy(
    dist: Path, field: bytes
) -> None:
    files = _wheel_files()
    files[f"{INFO}/METADATA"] = METADATA.rstrip(b"\n") + b"\n" + field + b"\n"
    # Rebuilding RECORD deliberately makes the archive internally authentic.
    # Its changed dependency policy must still disagree with the source release.
    _write_wheel(dist / WHEEL, files)
    with pytest.raises(release.ReleaseError, match="dependency metadata differs"):
        release.verify(dist)


def _matching_dependency_metadata(
    dist: Path, source: bytes, *, wheel: bytes | None = None, project: bytes
) -> None:
    _write_sdist(
        dist / SDIST,
        [
            ("unstd-3.1.0/PKG-INFO", source),
            ("unstd-3.1.0/pyproject.toml", project),
        ],
    )
    files = _wheel_files()
    files[f"{INFO}/METADATA"] = source if wheel is None else wheel
    _write_wheel(dist / WHEEL, files)


_EXTRA_PROJECT = b"""[project]
name = "unstd"
version = "3.1.0"
dependencies = []
[project.optional-dependencies]
fast = ["backend>=1", "another>=2"]
other = ["third>=3"]
"""
_EXTRA_HEADERS = [
    b"Requires-Python: >=3.12",
    b"License-Expression: Apache-2.0",
    b"Provides-Extra: fast",
    b"Provides-Extra: other",
    b"Requires-Dist: backend>=1; extra == 'fast'",
    b"Requires-Dist: another>=2; extra == 'fast'",
    b"Requires-Dist: third>=3; extra == 'other'",
]


def _extra_metadata(headers: list[bytes]) -> bytes:
    return METADATA.rstrip(b"\n") + b"\n" + b"\n".join(headers) + b"\n\n"


def test_metadata_list_order_can_differ_across_build_routes(dist: Path) -> None:
    _matching_dependency_metadata(
        dist,
        _extra_metadata(_EXTRA_HEADERS),
        wheel=_extra_metadata(list(reversed(_EXTRA_HEADERS))),
        project=_EXTRA_PROJECT,
    )
    assert release.verify(dist)["name"] == "unstd"


def test_requirement_content_is_compared_exactly_across_build_routes(
    dist: Path,
) -> None:
    source = _extra_metadata(_EXTRA_HEADERS)
    _matching_dependency_metadata(
        dist,
        source,
        wheel=source.replace(b"backend>=1", b"backend >=1"),
        project=_EXTRA_PROJECT,
    )
    with pytest.raises(release.ReleaseError, match="dependency metadata differs"):
        release.verify(dist)


def test_matching_archives_cannot_add_a_registry_dependency_absent_from_source(
    dist: Path,
) -> None:
    _matching_dependency_metadata(
        dist,
        _extra_metadata(
            [*_EXTRA_HEADERS, b"Requires-Dist: registry-surprise>=1; extra == 'fast'"]
        ),
        project=_EXTRA_PROJECT,
    )
    with pytest.raises(release.ReleaseError, match="dependencies differ from source"):
        release.verify(dist)


def test_matching_archives_cannot_add_an_unconditional_base_dependency(
    dist: Path,
) -> None:
    _matching_dependency_metadata(
        dist,
        _extra_metadata([*_EXTRA_HEADERS, b"Requires-Dist: registry-surprise>=1"]),
        project=_EXTRA_PROJECT,
    )
    with pytest.raises(release.ReleaseError, match="dependencies differ from source"):
        release.verify(dist)


def test_source_manifest_cannot_change_the_zero_dependency_base_contract(
    dist: Path,
) -> None:
    _matching_dependency_metadata(
        dist,
        _extra_metadata([*_EXTRA_HEADERS, b"Requires-Dist: registry-surprise>=1"]),
        project=_EXTRA_PROJECT.replace(
            b"dependencies = []", b'dependencies = ["registry-surprise>=1"]'
        ),
    )
    with pytest.raises(release.ReleaseError, match="must have no base dependencies"):
        release.verify(dist)


@pytest.mark.parametrize(
    "field", [b"Requires-Python: >=3.12", b"License-Expression: Apache-2.0"]
)
def test_duplicate_scalar_policy_fields_fail_even_when_archives_agree(
    dist: Path, field: bytes
) -> None:
    _matching_dependency_metadata(
        dist,
        _extra_metadata([*_EXTRA_HEADERS, field]),
        project=_EXTRA_PROJECT,
    )
    with pytest.raises(release.ReleaseError, match="duplicate metadata field"):
        release.verify(dist)


def test_source_extra_cannot_introduce_a_direct_url_dependency(dist: Path) -> None:
    source_requirement = b"backend @ https://example.invalid/backend.whl"
    _matching_dependency_metadata(
        dist,
        _extra_metadata(
            [
                header.replace(b"backend>=1", source_requirement)
                for header in _EXTRA_HEADERS
            ]
        ),
        project=_EXTRA_PROJECT.replace(b"backend>=1", source_requirement),
    )
    with pytest.raises(release.ReleaseError, match="simple registry requirements"):
        release.verify(dist)


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/drive",
        "unstd\\escape",
        "unstd//alias",
        "unstd/./alias",
        "unstd/a/../../escape",
        "unstd/control\n",
    ],
)
def test_unsafe_wheel_paths_fail_without_extracting(dist: Path, name: str) -> None:
    files = _wheel_files()
    files[name] = b"hostile"
    _write_wheel(dist / WHEEL, files)
    with pytest.raises(release.ReleaseError, match="unsafe archive path"):
        release.verify(dist)


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "unstd-3.1.0/../escape",
        "unstd-3.1.0\\escape",
        "another-root/file",
    ],
)
def test_unsafe_or_unconfined_sdist_paths_fail(dist: Path, name: str) -> None:
    _write_sdist(dist / SDIST, [(name, b"hostile")])
    with pytest.raises(
        release.ReleaseError, match=r"unsafe archive path|unexpected archive root"
    ):
        release.verify(dist)


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE]
)
def test_sdist_links_and_special_files_fail(dist: Path, kind: bytes) -> None:
    member = tarfile.TarInfo("unstd-3.1.0/hostile")
    member.type = kind
    member.linkname = "../../escape"
    _write_sdist(dist / SDIST, [(member.name, member)])
    with pytest.raises(release.ReleaseError, match="non-regular sdist member"):
        release.verify(dist)


def test_wheel_symlink_fails(dist: Path) -> None:
    member = zipfile.ZipInfo("unstd/link")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(dist / WHEEL, "a") as archive:
        archive.writestr(member, b"../../escape")
    with pytest.raises(release.ReleaseError, match="non-regular wheel member"):
        release.verify(dist)


def test_duplicate_wheel_member_fails(dist: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(dist / WHEEL, "a") as archive:
            archive.writestr("unstd/__init__.py", b"replacement")
    with pytest.raises(release.ReleaseError, match="duplicate archive member"):
        release.verify(dist)


def test_duplicate_sdist_member_fails(dist: Path) -> None:
    _write_sdist(dist / SDIST, [("unstd-3.1.0/PKG-INFO", METADATA)] * 2)
    with pytest.raises(release.ReleaseError, match="duplicate archive member"):
        release.verify(dist)


def test_file_cannot_also_be_an_archive_directory(dist: Path) -> None:
    files = _wheel_files()
    files["unstd"] = b"file shadowing the package directory"
    _write_wheel(dist / WHEEL, files)
    with pytest.raises(release.ReleaseError, match="shadows directory"):
        release.verify(dist)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_hash",
        "wrong_size",
        "unsigned",
        "weak_hash",
        "missing",
        "extra",
        "duplicate",
        "self_hash",
        "malformed",
    ],
)
def test_record_tampering_fails(dist: Path, mutation: str) -> None:
    files = _wheel_files()
    rows = list(csv.reader(io.StringIO(_record(files).decode())))
    if mutation == "wrong_hash":
        rows[0][1] = "sha256=" + "A" * 43
    elif mutation == "wrong_size":
        rows[0][2] = "999"
    elif mutation == "unsigned":
        rows[0][1:] = ["", ""]
    elif mutation == "weak_hash":
        rows[0][1] = "md5=" + "A" * 22
    elif mutation == "missing":
        rows.pop(0)
    elif mutation == "extra":
        rows.append(["unstd/absent.py", "sha256=" + "A" * 43, "0"])
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "self_hash":
        rows[-1][1:] = ["sha256=" + "A" * 43, "0"]
    elif mutation == "malformed":
        rows[0].pop()
    text = io.StringIO(newline="")
    csv.writer(text).writerows(rows)
    files[RECORD] = text.getvalue().encode()
    _write_wheel(dist / WHEEL, files)
    with pytest.raises(release.ReleaseError, match="RECORD"):
        release.verify(dist)


def test_content_changed_after_record_was_created_fails(dist: Path) -> None:
    files = _wheel_files()
    files[RECORD] = _record(files)
    files["unstd/__init__.py"] = b"injected code\n"
    _write_wheel(dist / WHEEL, files)
    with pytest.raises(release.ReleaseError, match="RECORD hash or size mismatch"):
        release.verify(dist)


def test_compatibility_metadata_must_match_wheel_filename(dist: Path) -> None:
    files = _wheel_files()
    files[f"{INFO}/WHEEL"] = files[f"{INFO}/WHEEL"].replace(
        b"py3-none-any", b"cp313-cp313-win_amd64"
    )
    _write_wheel(dist / WHEEL, files)
    with pytest.raises(release.ReleaseError, match="compatibility tags mismatch"):
        release.verify(dist)


@pytest.mark.parametrize("filename", [WHEEL, SDIST])
def test_corrupt_archives_fail_closed(dist: Path, filename: str) -> None:
    (dist / filename).write_bytes(b"not an archive")
    with pytest.raises(release.ReleaseError, match="invalid release archive"):
        release.verify(dist)


def test_corrupt_deflate_stream_fails_as_a_release_error(dist: Path) -> None:
    path = dist / WHEEL
    with zipfile.ZipFile(path) as archive:
        member = archive.getinfo("unstd/__init__.py")
        data_offset = (
            member.header_offset
            + 30
            + len(member.filename.encode())
            + len(member.extra)
        )
    content = bytearray(path.read_bytes())
    content[data_offset] = 0xFF  # Reserved DEFLATE block type.
    path.write_bytes(content)
    with pytest.raises(release.ReleaseError, match="invalid release archive"):
        release.verify(dist)


def test_unsupported_zip_compression_fails_as_a_release_error(dist: Path) -> None:
    path = dist / WHEEL
    content = bytearray(path.read_bytes())
    local = content.index(b"PK\x03\x04")
    central = content.index(b"PK\x01\x02")
    content[local + 8 : local + 10] = (99).to_bytes(2, "little")
    content[central + 10 : central + 12] = (99).to_bytes(2, "little")
    path.write_bytes(content)
    with pytest.raises(release.ReleaseError, match="invalid release archive"):
        release.verify(dist)


def test_archive_work_is_bounded(dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "MAX_UNPACKED_BYTES", 10)
    with pytest.raises(release.ReleaseError, match="size limit"):
        release.verify(dist)


def test_standalone_isolated_cli_creates_and_checks_expected_manifest(
    dist: Path, tmp_path: Path
) -> None:
    verifier = tmp_path / "release.py"
    shutil.copyfile(release.__file__, verifier)
    manifest = tmp_path / "evidence" / "manifest.json"
    argv = [sys.executable, "-I", str(verifier), "verify", "--dist", str(dist)]
    generated = subprocess.run(  # noqa: S603 — literal argv, local fixture, bounded process
        [*argv, "--manifest", str(manifest)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    assert (
        json.loads(generated.stdout)
        == json.loads(manifest.read_text())
        == release.verify(dist)
    )
    verified = subprocess.run(  # noqa: S603 — literal argv, local fixture, bounded process
        [*argv, "--expected-manifest", str(manifest), "--tag", "v3.1.0"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    files = _wheel_files()
    files["unstd/__init__.py"] = b"changed but internally valid wheel\n"
    _write_wheel(dist / WHEEL, files)
    rejected = subprocess.run(  # noqa: S603 — literal argv, local fixture, bounded process
        [*argv, "--expected-manifest", str(manifest)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert rejected.returncode == 1
    assert "artifacts do not match expected manifest" in rejected.stderr


def test_manifest_cannot_pollute_the_publication_directory(
    dist: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        release.main(
            ["verify", "--dist", str(dist), "--manifest", str(dist / "manifest.json")]
        )
        == 1
    )
    assert "manifest must be outside dist" in capsys.readouterr().err
    assert not (dist / "manifest.json").exists()


@pytest.mark.parametrize("content", ["{broken", '{"schema_version":true}', "[]"])
def test_invalid_or_nonmatching_expected_manifest_fails(
    dist: Path, tmp_path: Path, content: str
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(content)
    assert (
        release.main(
            ["verify", "--dist", str(dist), "--expected-manifest", str(manifest)]
        )
        == 1
    )
