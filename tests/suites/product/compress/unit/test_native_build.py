"""Build-only alternate inflaters are explicit and retain private symbols."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from tests.support.environment.anchors import REPO_ROOT


@pytest.fixture
def build_hook():
    pytest.importorskip("hatchling")
    pytest.importorskip("setuptools")
    spec = importlib.util.spec_from_file_location(
        "unstd_test_native_build", REPO_ROOT / "hatch_build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def clean_selection(monkeypatch):
    for name in (
        "UNSTD_GZIP_ZLIB_RS",
        "UNSTD_GZIP_ZLIB_RS_SHA256",
        "UNSTD_GZIP_ZLIB_RS_LINK_ARGS",
        "CIBUILDWHEEL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("digest", [None, "", "abc", "z" * 64])
def test_alternate_inflater_requires_explicit_expected_digest(
    build_hook, tmp_path, monkeypatch, digest
):
    archive = tmp_path / "libz_rs.a"
    archive.write_bytes(b"reviewed archive")
    monkeypatch.setenv("UNSTD_GZIP_ZLIB_RS", str(archive))
    if digest is not None:
        monkeypatch.setenv("UNSTD_GZIP_ZLIB_RS_SHA256", digest)
    with pytest.raises(ValueError, match="expected SHA256"):
        build_hook.CustomBuildHook._zlib_rs_archive(tmp_path / "staged")


def test_alternate_inflater_detects_archive_replacement(
    build_hook, tmp_path, monkeypatch
):
    archive = tmp_path / "libz_rs.a"
    archive.write_bytes(b"reviewed archive")
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setenv("UNSTD_GZIP_ZLIB_RS", str(archive))
    monkeypatch.setenv("UNSTD_GZIP_ZLIB_RS_SHA256", expected.upper())
    staged = build_hook.CustomBuildHook._zlib_rs_archive(tmp_path / "staged")
    assert staged.parent == tmp_path / "staged"
    assert staged != archive.resolve()
    assert staged.read_bytes() == b"reviewed archive"
    archive.write_bytes(b"unexpected replacement")
    assert staged.read_bytes() == b"reviewed archive"
    with pytest.raises(ValueError, match="does not match"):
        build_hook.CustomBuildHook._zlib_rs_archive(tmp_path / "rejected")
    assert list((tmp_path / "rejected").iterdir()) == []


def test_experimental_archive_cannot_enter_the_release_wheel_lane(
    build_hook, tmp_path, monkeypatch
):
    monkeypatch.setenv("UNSTD_GZIP_ZLIB_RS", "libz_rs.a")
    monkeypatch.setenv("CIBUILDWHEEL", "1")
    with pytest.raises(ValueError, match="source/license/SBOM closure"):
        build_hook.CustomBuildHook._zlib_rs_archive(tmp_path / "staged")


def test_failed_archive_copy_preserves_error_and_removes_partial_file(
    build_hook, tmp_path, monkeypatch
):
    archive = tmp_path / "libz_rs.a"
    archive.write_bytes(b"reviewed archive")
    monkeypatch.setenv("UNSTD_GZIP_ZLIB_RS", str(archive))
    monkeypatch.setenv(
        "UNSTD_GZIP_ZLIB_RS_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    failure = OSError("source copy failed")

    def partial(_source, destination):
        destination.write(b"partial unverified archive")
        raise failure

    monkeypatch.setattr(build_hook.shutil, "copyfileobj", partial)
    with pytest.raises(OSError, match="source copy failed") as caught:
        build_hook.CustomBuildHook._zlib_rs_archive(tmp_path / "staged")
    assert caught.value is failure
    assert list((tmp_path / "staged").iterdir()) == []


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("alternate", [False, True])
def test_build_selection_preserves_default_and_never_mixes_inflater_sources(
    build_hook, tmp_path, monkeypatch, platform, alternate
):
    selected = None
    if alternate:
        selected = tmp_path / "libz_rs.a"
        selected.write_bytes(b"reviewed archive")
        monkeypatch.setenv("UNSTD_GZIP_ZLIB_RS", str(selected))
        monkeypatch.setenv(
            "UNSTD_GZIP_ZLIB_RS_SHA256",
            hashlib.sha256(selected.read_bytes()).hexdigest(),
        )
        monkeypatch.setenv("UNSTD_GZIP_ZLIB_RS_LINK_ARGS", "-lm")
    captured = []
    original = build_hook.Extension

    def extension(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append(result)
        return result

    class Compile:
        def __init__(self, _distribution):
            pass

        def ensure_finalized(self):
            pass

        def run(self):
            pass

        def get_ext_fullpath(self, _name):
            return str(tmp_path / "not-built.so")

    monkeypatch.setattr(build_hook, "Extension", extension)
    monkeypatch.setattr(build_hook, "_IsalBuildExt", Compile)
    monkeypatch.setattr(build_hook.sys, "platform", platform)
    hook = build_hook.CustomBuildHook(
        str(REPO_ROOT), {}, None, None, str(tmp_path), "wheel"
    )
    hook._build_module(
        "unstd.compress._gzip_native", tmp_path / "compile", "standard", {}
    )
    (actual,) = captured
    assert actual.extra_compile_args == []
    bundled = any("/compress/vendor/zlib/" in source for source in actual.sources)
    if alternate:
        assert actual.define_macros == [("UNSTD_ZLIB_RS", None)]
        (staged,) = actual.extra_objects
        assert Path(staged).is_relative_to(tmp_path / "compile/reviewed-zlib-rs")
        assert Path(staged).read_bytes() == selected.read_bytes()
        assert actual.extra_link_args == ["-lm"]
        assert not actual.libraries
        assert not bundled
        assert not actual.optional
    else:
        assert not actual.extra_objects
        assert not actual.extra_link_args
        assert actual.libraries == (["z"] if platform == "darwin" else [])
        assert bundled is (platform != "darwin")
        assert actual.define_macros == (
            [] if platform == "darwin" else [("Z_PREFIX", None), ("MY_ZCALLOC", None)]
        )


@pytest.fixture
def qualification(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "unstd_test_libdeflate_qualification",
        REPO_ROOT / "native/compress/experiments/qualify_libdeflate.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = tmp_path / "upstream"
    source.mkdir()
    (source / "COPYING").write_bytes(b"reviewed license")
    raw = (
        b'{"files":{"COPYING":"'
        + hashlib.sha256(b"reviewed license").hexdigest().encode()
        + b'"}}'
    )
    manifest = tmp_path / "review.json"
    manifest.write_bytes(raw)
    monkeypatch.setattr(module, "MANIFEST", manifest)
    monkeypatch.setattr(module, "MANIFEST_SHA256", hashlib.sha256(raw).hexdigest())
    return module, source


def test_qualification_stage_authenticates_copied_bytes(qualification, tmp_path):
    module, source = qualification
    staged = tmp_path / "verified"
    module.stage_source(source, staged)
    (source / "COPYING").write_bytes(b"unreviewed replacement")
    assert (staged / "COPYING").read_bytes() == b"reviewed license"
    rejected = tmp_path / "rejected"
    with pytest.raises(ValueError, match="source hash mismatch"):
        module.stage_source(source, rejected)
    assert not rejected.exists()


def test_qualification_rejects_changed_review_manifest(qualification, tmp_path):
    module, source = qualification
    module.MANIFEST.write_bytes(b'{"files":{}}')
    with pytest.raises(ValueError, match="review manifest changed"):
        module.stage_source(source, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
