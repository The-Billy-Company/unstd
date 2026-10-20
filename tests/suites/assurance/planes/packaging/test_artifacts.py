"""Publication contents, metadata, provenance and RECORD integrity of real builds."""

from __future__ import annotations

import base64
import csv
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
import os
from pathlib import PurePosixPath
import re
import shutil
import sys
import tarfile
import tomllib
from typing import TYPE_CHECKING
import zipfile

from packaging.utils import parse_wheel_filename
import pytest

from tests.support.anchors import REPO_ROOT
from tests.support.packaging import (
    checked_command,
    clean_environment,
    create_consumer,
    uv_executable,
)


if TYPE_CHECKING:
    from email.message import Message
    from pathlib import Path

    from tests.support.packaging import Artifacts


with (REPO_ROOT / "pyproject.toml").open("rb") as _manifest:
    _EXTRAS = tuple(sorted(tomllib.load(_manifest)["project"]["optional-dependencies"]))


def _wheel_files(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)), "duplicate ZIP members"
        return {name: archive.read(name) for name in names if not name.endswith("/")}


def _metadata(wheel: Path) -> Message:
    files = _wheel_files(wheel)
    names = [name for name in files if name.endswith(".dist-info/METADATA")]
    assert len(names) == 1, names
    return BytesParser(policy=default).parsebytes(files[names[0]])


def _native_file(name: str) -> bool:
    return bool(re.fullmatch(r"unstd/serde/_json_native(?:\.abi3)?\.(?:so|pyd)", name))


def _requirement(requirement: str) -> str:
    # Distribution names are PEP 503 normalized; metadata may normalize them
    # independently from the manifest. Version specifiers must remain exact.
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)(.*)", requirement)
    assert match is not None, requirement
    name, specifier = match.groups()
    return re.sub(r"[-_.]+", "-", name).lower() + specifier.replace(" ", "")


def test_wheel_contains_every_source_module_and_data_file(
    wheel: Path, artifacts: Artifacts
) -> None:
    files = _wheel_files(wheel)
    actual = {
        name: value
        for name, value in files.items()
        if name.startswith("unstd/") and not _native_file(name)
    }
    assert actual == artifacts.package_files
    assert "unstd/py.typed" in actual
    assert actual["unstd/py.typed"] == b""
    assert not any(
        part in {"tests", "bench", ".venv", "__pycache__"}
        for name in files
        for part in PurePosixPath(name).parts
    )
    binaries = [
        name for name in files if name.endswith((".pyc", ".so", ".pyd", ".dylib"))
    ]
    assert len(binaries) <= 1
    assert all(_native_file(name) for name in binaries)


def test_wheel_paths_are_safe_and_confined_to_package_or_dist_info(wheel: Path) -> None:
    for name in _wheel_files(wheel):
        path = PurePosixPath(name)
        assert not path.is_absolute()
        assert ".." not in path.parts
        assert "\\" not in name
        assert path.parts[0] == "unstd" or path.parts[0].endswith(".dist-info")


def test_wheel_record_authenticates_every_installed_file(wheel: Path) -> None:
    files = _wheel_files(wheel)
    records = [name for name in files if name.endswith(".dist-info/RECORD")]
    assert len(records) == 1
    record = records[0]
    entries = list(csv.reader(io.StringIO(files[record].decode("utf-8"))))
    assert len(entries) == len(files)
    assert {entry[0] for entry in entries} == files.keys()
    assert len({entry[0] for entry in entries}) == len(entries)
    for name, digest, length in entries:
        if name == record:
            assert digest == length == ""
        else:
            expected = base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest())
            assert digest == "sha256=" + expected.rstrip(b"=").decode("ascii")
            assert length == str(len(files[name]))


def test_wheel_tags_match_native_or_portable_contents(wheel: Path) -> None:
    files = _wheel_files(wheel)
    descriptor = next(value for name, value in files.items() if name.endswith("/WHEEL"))
    metadata = BytesParser(policy=default).parsebytes(descriptor)
    _, _, _, tags = parse_wheel_filename(wheel.name)
    native = any(_native_file(name) for name in files)
    assert metadata["Root-Is-Purelib"] == str(not native).lower()
    assert set(metadata.get_all("Tag", [])) == {str(tag) for tag in tags}
    if native:
        assert all(tag.interpreter == "cp312" and tag.abi == "abi3" for tag in tags)
        assert all(tag.platform != "any" for tag in tags)
    else:
        assert metadata.get_all("Tag") == ["py3-none-any"]
        assert wheel.name.endswith("-py3-none-any.whl")


def test_wheel_metadata_identity_python_license_and_description(
    wheel: Path, artifacts: Artifacts
) -> None:
    metadata = _metadata(wheel)
    project = artifacts.project
    assert metadata["Name"] == project["name"] == "unstd"
    assert metadata["Version"] == project["version"]
    assert metadata["Requires-Python"] == project["requires-python"]
    assert metadata["Summary"] == project["description"]
    assert metadata["License-Expression"] == project["license"] == "Apache-2.0"
    assert set(metadata.get_all("License-File", [])) == {"LICENSE", "NOTICE"}
    assert metadata["Description-Content-Type"] == "text/markdown"
    assert (
        metadata.get_payload(decode=True).decode("utf-8").strip()
        == (artifacts.source / "README.md").read_text(encoding="utf-8").strip()
    )
    files = _wheel_files(wheel)
    for name in ("LICENSE", "NOTICE"):
        matches = [
            value for path, value in files.items() if path.endswith(f"/licenses/{name}")
        ]
        assert matches == [(artifacts.source / name).read_bytes()]


def test_wheel_declares_only_the_intended_extras_and_no_base_dependencies(
    wheel: Path, artifacts: Artifacts
) -> None:
    metadata = _metadata(wheel)
    extras = artifacts.project["optional-dependencies"]
    assert isinstance(extras, dict)
    assert set(metadata.get_all("Provides-Extra", [])) == set(extras) == set(_EXTRAS)
    for requirement in metadata.get_all("Requires-Dist", []):
        _, separator, marker = requirement.partition(";")
        assert separator, f"unexpected unconditional runtime dependency: {requirement}"
        assert re.fullmatch(r"\s*extra == ['\"][a-z]+['\"]\s*", marker), requirement


@pytest.mark.parametrize("extra", _EXTRAS)
def test_each_extra_has_its_independent_published_dependency_closure(
    extra: str, wheel: Path, artifacts: Artifacts
) -> None:
    extras = artifacts.project["optional-dependencies"]
    assert isinstance(extras, dict)
    expected = {_requirement(value) for value in extras[extra]}
    actual = set()
    for requirement in _metadata(wheel).get_all("Requires-Dist", []):
        dependency, _, marker = requirement.partition(";")
        if re.fullmatch(rf"\s*extra == ['\"]{extra}['\"]\s*", marker):
            actual.add(_requirement(dependency.strip()))
    assert actual == expected


def test_sdist_contains_all_sources_and_rebuild_inputs_without_local_caches(
    artifacts: Artifacts,
) -> None:
    with tarfile.open(artifacts.sdist, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        assert len(names) == len(set(names))
        roots = {PurePosixPath(name).parts[0] for name in names}
        assert roots == {f"unstd-{artifacts.project['version']}"}
        files = {}
        for member in members:
            path = PurePosixPath(member.name)
            assert not path.is_absolute()
            assert ".." not in path.parts
            assert not member.issym()
            assert not member.islnk()
            assert not set(path.parts) & {
                ".venv",
                ".git",
                "__pycache__",
                ".pytest_cache",
                ".hypothesis",
                ".mypy_cache",
                ".ruff_cache",
                ".env",
            }
            assert not member.name.endswith(".pyc")
            if member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                files[path.relative_to(next(iter(roots))).as_posix()] = stream.read()
    for required in (
        "pyproject.toml",
        "hatch_build.py",
        "README.md",
        "LICENSE",
        "NOTICE",
    ):
        assert files[required] == (artifacts.source / required).read_bytes()
    for name, content in artifacts.package_files.items():
        assert files[f"src/{name}"] == content
    metadata = BytesParser(policy=default).parsebytes(files["PKG-INFO"])
    assert metadata["Name"] == "unstd"
    assert metadata["Version"] == artifacts.project["version"]
    assert set(metadata.get_all("Provides-Extra", [])) == set(_EXTRAS)
    assert sorted(metadata.get_all("Requires-Dist", [])) == sorted(
        _metadata(artifacts.wheel).get_all("Requires-Dist", [])
    )


def test_wheel_rebuilt_from_sdist_preserves_the_entire_payload(
    artifacts: Artifacts,
) -> None:
    # ZIP timestamps/compression and independently compiled native binaries can
    # differ. Source, data, metadata, licenses and typing markers must survive.
    # Each route independently authenticates its entire RECORD above.
    files = [
        {
            name: value
            for name, value in _wheel_files(wheel).items()
            if not _native_file(name) and not name.endswith(".dist-info/RECORD")
        }
        for wheel in (artifacts.wheel, artifacts.sdist_wheel)
    ]
    assert files[0] == files[1]


@pytest.mark.skipif(os.name == "nt", reason="MSVC does not choose its compiler via CC")
def test_missing_compiler_builds_a_portable_fallback(
    artifacts: Artifacts, tmp_path: Path
) -> None:
    output = tmp_path / "without-compiler"
    checked_command(
        [
            uv_executable(),
            "build",
            "--offline",
            "--no-config",
            "--no-sources",
            "--no-python-downloads",
            "--python",
            sys.executable,
            "--wheel",
            "--out-dir",
            output,
            artifacts.source,
        ],
        cwd=tmp_path,
        env={**clean_environment(), "CC": str(tmp_path / "missing-compiler")},
    )
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    assert wheels[0].name.endswith("-py3-none-any.whl")
    assert not any(_native_file(name) for name in _wheel_files(wheels[0]))


@pytest.mark.skipif(os.name == "nt", reason="MSVC does not choose its compiler via CC")
def test_failed_editable_build_clears_its_stale_native_module(
    artifacts: Artifacts, tmp_path: Path
) -> None:
    source = tmp_path / "editable"
    shutil.copytree(artifacts.source, source)
    stale = source / "src/unstd/serde/_json_native.abi3.so"
    stale.write_bytes(b"stale compiler output")
    consumer = create_consumer(tmp_path / "consumer", artifacts.wheel)
    checked_command(
        [
            uv_executable(),
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            consumer.python,
            "--editable",
            source,
        ],
        cwd=tmp_path,
        env={**clean_environment(), "CC": str(tmp_path / "missing-compiler")},
    )
    assert not stale.exists()
    consumer.run("from unstd.serde import jsonx; assert jsonx.dumpb([1]) == b'[1]'")
