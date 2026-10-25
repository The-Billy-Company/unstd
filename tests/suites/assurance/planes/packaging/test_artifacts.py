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

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.packaging.artifacts import (
    checked_command,
    clean_environment,
    create_consumer,
    uv_executable,
)


if TYPE_CHECKING:
    from email.message import Message
    from pathlib import Path

    from tests.support.packaging.artifacts import Artifacts


with (REPO_ROOT / "pyproject.toml").open("rb") as _manifest:
    _EXTRAS = tuple(sorted(tomllib.load(_manifest)["project"]["optional-dependencies"]))


_NATIVE_MODULES = (
    "unstd.serde._json_native",
    "unstd.clone._clone_native",
    "unstd.crypto._stream_native",
    "unstd.text._text_native",
    "unstd.compress._gzip_native",
)


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
    return any(
        re.fullmatch(
            re.escape(module.replace(".", "/")) + r"(?:\.[a-zA-Z0-9_-]+)?\.(?:so|pyd)",
            name,
        )
        for module in _NATIVE_MODULES
    )


def _native_modules(files: dict[str, bytes]) -> set[str]:
    return {
        module
        for module in _NATIVE_MODULES
        if any(
            _native_file(name) and name.startswith(module.replace(".", "/") + ".")
            for name in files
        )
    }


def _requirement(requirement: str) -> str:
    # Distribution names are PEP 503 normalized; metadata may normalize them
    # independently from the manifest. Version specifiers must remain exact.
    parsed = Requirement(requirement)
    parsed.name = canonicalize_name(parsed.name)
    return str(parsed)


def _published_requirement(requirement: str, extra: str) -> str:
    parsed = Requirement(requirement)
    guard = f"extra == '{extra}'"
    parsed.marker = Marker(f"({parsed.marker}) and {guard}" if parsed.marker else guard)
    return _requirement(str(parsed))


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
    assert not any(name.endswith((".c", ".h", ".rs")) for name in actual)
    assert not any(
        part in {"tests", "bench", ".venv", "__pycache__"}
        for name in files
        for part in PurePosixPath(name).parts
    )
    binaries = [
        name for name in files if name.endswith((".pyc", ".so", ".pyd", ".dylib"))
    ]
    assert len(binaries) <= len(_NATIVE_MODULES)
    assert all(_native_file(name) for name in binaries)
    for module in _NATIVE_MODULES:
        prefix = module.replace(".", "/") + "."
        assert sum(name.startswith(prefix) for name in binaries) <= 1


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
        interpreter = f"cp{sys.version_info.major}{sys.version_info.minor}"
        assert all(
            tag.interpreter == interpreter and tag.abi == interpreter for tag in tags
        )
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
    assert (
        metadata["License-Expression"]
        == project["license"]
        == "Apache-2.0 AND MIT AND Zlib"
    )
    licenses = {
        "LICENSE",
        "NOTICE",
        "native/compress/vendor/libdeflate/COPYING",
        "native/compress/vendor/zlib/LICENSE",
    }
    assert set(metadata.get_all("License-File", [])) == licenses
    assert metadata["Description-Content-Type"] == "text/markdown"
    assert (
        metadata.get_payload(decode=True).decode("utf-8").strip()
        == (artifacts.source / "README.md").read_text(encoding="utf-8").strip()
    )
    files = _wheel_files(wheel)
    for name in licenses:
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
    expected = {
        _published_requirement(requirement, extra)
        for extra, requirements in extras.items()
        for requirement in requirements
    }
    actual = set()
    for requirement in metadata.get_all("Requires-Dist", []):
        parsed = Requirement(requirement)
        assert parsed.marker, (
            f"unexpected unconditional runtime dependency: {requirement}"
        )
        guards = re.findall(r"\bextra == ['\"]([a-z]+)['\"]", str(parsed.marker))
        assert len(guards) == 1, requirement
        assert guards[0] in extras, requirement
        actual.add(_requirement(requirement))
    assert actual == expected


@pytest.mark.parametrize("extra", _EXTRAS)
def test_each_extra_has_its_independent_published_dependency_closure(
    extra: str, wheel: Path, artifacts: Artifacts
) -> None:
    extras = artifacts.project["optional-dependencies"]
    assert isinstance(extras, dict)
    expected = {_published_requirement(value, extra) for value in extras[extra]}
    actual = set()
    for requirement in _metadata(wheel).get_all("Requires-Dist", []):
        parsed = Requirement(requirement)
        if re.search(rf"\bextra == ['\"]{extra}['\"]", str(parsed.marker)):
            actual.add(_requirement(requirement))
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
                "target",
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
        "docs/api.md",
        "docs/architecture.md",
        "native/text/rust/Cargo.toml",
        "native/text/rust/Cargo.lock",
        "native/text/rust/src/lib.rs",
        "native/text/rust/adapter.c",
        "native/text/src/adapter.c",
        "native/clone/src/adapter.c",
        "native/crypto/src/adapter.c",
        "native/serde/src/adapter.c",
        "native/serde/src/encode.h",
        "native/serde/src/decode.h",
        "native/serde/src/lines.h",
        "native/serde/src/escape.h",
        "native/compress/src/adapter.c",
        "native/compress/vendor/libdeflate/UPSTREAM.json",
        "native/compress/vendor/libdeflate/COPYING",
        "native/compress/vendor/zlib/LICENSE",
    ):
        assert files[required] == (artifacts.source / required).read_bytes()
    for source in (artifacts.source / "native").rglob("*"):
        if source.is_file():
            name = source.relative_to(artifacts.source).as_posix()
            assert files[name] == source.read_bytes()
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
    consumer = create_consumer(tmp_path / "consumer", wheels[0])
    consumer.run("""
from unstd import clone
from unstd.text import fuzz
from unstd.serde import jsonx
from unstd.compress import gzipx
import gzip
assert clone.deep({'items': [1, 2]}) == {'items': [1, 2]}
assert fuzz.distance('kitten', 'sitting') == 3
assert jsonx.dumpb([1]) == b'[1]'
assert gzipx.GzipFile is gzip.GzipFile
assert gzipx.decompress(gzip.compress(b'portable gzip')) == b'portable gzip'
""")


@pytest.mark.skipif(os.name == "nt", reason="MSVC does not choose its compiler via CC")
def test_failed_editable_build_clears_its_stale_native_module(
    artifacts: Artifacts, tmp_path: Path
) -> None:
    source = tmp_path / "editable"
    shutil.copytree(artifacts.source, source)
    stale = [
        source / "src" / (module.replace(".", "/") + ".abi3.so")
        for module in _NATIVE_MODULES
    ]
    for native in stale:
        native.write_bytes(b"stale compiler output")
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
    assert not any(native.exists() for native in stale)
    consumer.run("""
from unstd import clone
from unstd.text import fuzz
from unstd.serde import jsonx
from unstd.compress import gzipx
import gzip
assert clone.deep({'items': [1, 2]}) == {'items': [1, 2]}
assert fuzz.distance('kitten', 'sitting') == 3
assert jsonx.dumpb([1]) == b'[1]'
assert gzipx.GzipFile is gzip.GzipFile
assert gzipx.decompress(gzip.compress(b'portable gzip')) == b'portable gzip'
""")


@pytest.mark.parametrize("failed_module", _NATIVE_MODULES)
def test_optional_compiler_failure_preserves_other_native_modules(
    artifacts: Artifacts, tmp_path: Path, failed_module: str
) -> None:
    if _native_modules(_wheel_files(artifacts.wheel)) != set(_NATIVE_MODULES):
        pytest.skip("requires a compiler that can build every native module")
    source = tmp_path / "partial"
    shutil.copytree(artifacts.source, source)
    implementation = (
        source / "native" / failed_module.split(".")[1] / "src" / "adapter.c"
    )
    implementation.write_text("#error unstd_test_native_failure\n", encoding="utf-8")
    output = tmp_path / "output"
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
            source,
        ],
        cwd=tmp_path,
    )
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    assert _native_modules(_wheel_files(wheels[0])) == set(_NATIVE_MODULES) - {
        failed_module
    }
    assert not wheels[0].name.endswith("-py3-none-any.whl")
    strict = checked_command(
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
            tmp_path / "strict",
            source,
        ],
        cwd=tmp_path,
        env={**clean_environment(), "CIBUILDWHEEL": "1"},
        expected_code=2,
    )
    assert "unstd_test_native_failure" in strict.stdout + strict.stderr
    assert not list((tmp_path / "strict").glob("*.whl"))


@pytest.mark.parametrize("failure", ["missing-cargo", "invalid-rust"])
def test_optional_rust_failure_preserves_c_modules_and_fails_release_builds(
    artifacts: Artifacts, tmp_path: Path, failure: str
) -> None:
    if _native_modules(_wheel_files(artifacts.wheel)) != set(_NATIVE_MODULES):
        pytest.skip("requires a compiler that can build every C native module")
    if failure == "invalid-rust" and shutil.which("cargo") is None:
        pytest.skip("requires Cargo to exercise a Rust compilation failure")
    source = tmp_path / "partial-rust"
    shutil.copytree(artifacts.source, source)
    environment = {**clean_environment(), "UNSTD_TEXT_RUST": "1"}
    if failure == "missing-cargo":
        marker = "unstd-missing-cargo"
        environment["CARGO"] = str(tmp_path / marker)
    else:
        marker = "unstd_test_rust_failure"
        (source / "native/text/rust/src/lib.rs").write_text(
            f'compile_error!("{marker}");\n', encoding="utf-8"
        )
    common = [
        uv_executable(),
        "build",
        "--offline",
        "--no-config",
        "--no-sources",
        "--no-python-downloads",
        "--python",
        sys.executable,
        "--wheel",
        source,
    ]
    output = tmp_path / "output"
    checked_command([*common, "--out-dir", output], cwd=tmp_path, env=environment)
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    expected = set(_NATIVE_MODULES) - {"unstd.text._text_native"}
    if failure == "missing-cargo" and (source / "native/compress/Cargo.toml").is_file():
        expected.remove("unstd.compress._gzip_native")
    assert _native_modules(_wheel_files(wheels[0])) == expected
    consumer = create_consumer(tmp_path / "consumer", wheels[0])
    consumer.run("""
from unstd.text import fuzz
assert fuzz.distance('kitten', 'sitting') == 3
assert fuzz.jaro_winkler('MARTHA', 'MARHTA') == 0.9611111111111111
""")
    strict = checked_command(
        [*common, "--out-dir", tmp_path / "strict"],
        cwd=tmp_path,
        env={**environment, "CIBUILDWHEEL": "1"},
        expected_code=2,
    )
    assert marker in strict.stdout + strict.stderr
    assert not list((tmp_path / "strict").glob("*.whl"))


def test_missing_cargo_editable_build_clears_only_stale_text_module(
    artifacts: Artifacts, tmp_path: Path
) -> None:
    if _native_modules(_wheel_files(artifacts.wheel)) != set(_NATIVE_MODULES):
        pytest.skip("requires a compiler that can build every C native module")
    source = tmp_path / "editable-rust"
    shutil.copytree(artifacts.source, source)
    suffix = ".pyd" if os.name == "nt" else ".so"
    stale = source / "src/unstd/text" / ("_text_native.abi3" + suffix)
    stale.write_bytes(b"stale Rust compiler output")
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
        env={
            **clean_environment(),
            "CARGO": str(tmp_path / "missing-cargo"),
            "UNSTD_TEXT_RUST": "1",
        },
    )
    assert not stale.exists()
    for module in set(_NATIVE_MODULES) - {"unstd.text._text_native"}:
        relative = module.replace(".", "/")
        assert any(
            path.suffix in {".so", ".pyd"}
            for path in (source / "src" / relative).parent.glob(
                relative.split("/")[-1] + ".*"
            )
        )
    assert not list((source / "src/unstd/text").glob("_text_native.*" + suffix))


def test_default_c_build_does_not_require_cargo(
    artifacts: Artifacts, tmp_path: Path
) -> None:
    if _native_modules(_wheel_files(artifacts.wheel)) != set(_NATIVE_MODULES):
        pytest.skip("requires a compiler that can build every C native module")
    output = tmp_path / "default-without-cargo"
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
        env={
            **clean_environment(),
            "CARGO": str(tmp_path / "missing-cargo"),
            "CIBUILDWHEEL": "1",
        },
    )
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    assert _native_modules(_wheel_files(wheels[0])) == set(_NATIVE_MODULES)


def test_opted_in_rust_build_from_sdist_has_no_extra_runtime_dependency(
    artifacts: Artifacts, tmp_path: Path
) -> None:
    if _native_modules(_wheel_files(artifacts.wheel)) != set(_NATIVE_MODULES):
        pytest.skip("requires a compiler that can build every C native module")
    if shutil.which("cargo") is None:
        pytest.skip("requires Cargo to build the opt-in Rust experiment")
    output = tmp_path / "rust-from-sdist"
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
            artifacts.sdist,
        ],
        cwd=tmp_path,
        env={**clean_environment(), "UNSTD_TEXT_RUST": "1", "CIBUILDWHEEL": "1"},
    )
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    assert _native_modules(_wheel_files(wheels[0])) == set(_NATIVE_MODULES)
    assert _metadata(wheels[0]).get_all("Requires-Dist", []) == _metadata(
        artifacts.wheel
    ).get_all("Requires-Dist", [])
    consumer = create_consumer(tmp_path / "consumer", wheels[0])
    consumer.run("""
import importlib.metadata
from unstd.text import _text_native, fuzz
assert {dist.metadata['Name'].lower() for dist in importlib.metadata.distributions()} == {'unstd'}
assert 'Rust text kernels' in _text_native.__doc__
assert _text_native.distance('kitten', 'sitting') == 3
assert fuzz.jaro_winkler('MARTHA', 'MARHTA') == 0.9611111111111111
""")
