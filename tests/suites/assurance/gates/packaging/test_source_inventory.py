"""Frozen source completeness must not depend on the builder's host platform."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys

import pytest

from tests.support.packaging import artifacts


def _source(root):
    root.mkdir()
    (root / "hatch_build.py").write_text(
        "_NATIVE_MODULES = ('unstd.clone._clone_native', 'unstd.fs._fs_native')\n"
        "raise RuntimeError('inventory checks must not execute the build hook')\n"
    )
    for owner in ("clone", "fs"):
        adapter = root / "native" / owner / "src/adapter.c"
        adapter.parent.mkdir(parents=True)
        adapter.write_text("/* synthetic registered adapter */\n")
    return root


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_frozen_source_requires_linux_adapter_even_on_other_hosts(
    tmp_path, monkeypatch, platform
):
    live = _source(tmp_path / "live")
    frozen = tmp_path / "frozen"
    shutil.copytree(live, frozen)
    (frozen / "native/fs/src/adapter.c").unlink()
    assert (live / "native/fs/src/adapter.c").is_file()
    monkeypatch.setattr(sys, "platform", platform)
    with pytest.raises(AssertionError, match=r"native/fs/src/adapter\.c"):
        artifacts.require_native_adapters(frozen)


def test_complete_frozen_inventory_uses_its_own_adapter_bytes(tmp_path):
    source = _source(tmp_path / "frozen")
    adapters = artifacts.require_native_adapters(source)
    assert adapters == (
        source / "native/clone/src/adapter.c",
        source / "native/fs/src/adapter.c",
    )
    assert all(path.read_bytes() for path in adapters)


def test_artifact_builder_checks_copied_sources_before_any_build(tmp_path, monkeypatch):
    live = _source(tmp_path / "live")
    (live / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n")
    (live / "src/unstd").mkdir(parents=True)
    (live / "src/unstd/__init__.py").write_text("")
    monkeypatch.setattr(artifacts, "REPO_ROOT", live)
    copytree = shutil.copytree

    def incomplete_copy(source, destination, *args, **kwargs):
        result = copytree(source, destination, *args, **kwargs)
        if Path(destination).name == "native":
            (Path(destination) / "fs/src/adapter.c").unlink()
        return result

    def unexpected_build():
        pytest.fail("a build started before the frozen source inventory was checked")

    monkeypatch.setattr(shutil, "copytree", incomplete_copy)
    monkeypatch.setattr(artifacts, "uv_executable", unexpected_build)
    with pytest.raises(AssertionError, match=r"native/fs/src/adapter\.c"):
        artifacts.build_artifacts(tmp_path / "artifacts")
