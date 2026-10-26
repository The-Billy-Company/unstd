"""Real cibuildwheel sdist extraction must preserve every constraint-file boundary."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.backends import require_backend


@pytest.mark.parametrize(
    ("platform", "architecture", "identifier"),
    [
        ("linux", "aarch64", "cp313-manylinux_aarch64"),
        ("macos", "arm64", "cp313-macosx_arm64"),
        ("windows", "AMD64", "cp313-win_amd64"),
    ],
)
@pytest.mark.parametrize(
    "absolute", [False, True], ids=["relative-rejected", "absolute"]
)
def test_sdist_constraints_survive_real_builder_directory_changes(
    tmp_path, monkeypatch, platform, architecture, identifier, absolute
):
    builder = pytest.importorskip("cibuildwheel.__main__")
    from cibuildwheel.errors import ConfigurationError
    from cibuildwheel.frontend import get_build_frontend_extra_flags
    from cibuildwheel.util.helpers import prepare_command

    yaml = require_backend("ruamel.yaml").YAML(typ="safe", pure=True)
    workflow = yaml.load((REPO_ROOT / ".github/workflows/release.yml").read_text())
    build = next(
        step
        for step in workflow["jobs"]["wheels"]["steps"]
        if "CIBW_BUILD" in step.get("env", {})
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = tmp_path / "source-probe"
    source.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(REPO_ROOT / name, source / name)
    archive = checkout / "probe.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(source, arcname="source-probe")
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()
    constraints = runner_temp / "release-constraints.txt"
    constraints.write_text("cibuildwheel==4.3.1\n", encoding="utf-8")
    shutil.copyfile(constraints, checkout / constraints.name)
    for name in list(os.environ):
        if name.startswith("CIBW_"):
            monkeypatch.delenv(name)
    for name, value in build["env"].items():
        monkeypatch.setenv(
            name, str(value).replace("${{ runner.temp }}", str(runner_temp))
        )
    monkeypatch.setenv("CIBW_BUILD", "cp313-*")
    if not absolute:
        monkeypatch.setenv("CIBW_DEPENDENCY_VERSIONS", constraints.name)
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(builder, "CIBW_CACHE_PATH", tmp_path / "builder-cache")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cibuildwheel",
            str(archive),
            "--platform",
            platform,
            "--archs",
            architecture,
            "--output-dir",
            str(tmp_path / "wheels"),
        ],
    )
    uv = shutil.which("uv")
    assert uv is not None
    observed = []

    def inspect_build(options, _temporary):
        package = options.globals.package_dir
        assert Path.cwd() == package
        assert package != source
        assert package != checkout
        selected = options.build_options(identifier)
        assert selected.dependency_constraints.base_file_path == constraints.resolve()
        environment = selected.test_environment.as_dictionary(prev_environment={})
        if platform == "linux":
            assert (
                environment["_PYTHON_SYSCONFIGDATA_PATH"]
                == "/tmp/unstd-wheel-compiler.sysconfig"  # noqa: S108 — disposable build container
            )
        else:
            assert "_PYTHON_SYSCONFIGDATA_PATH" not in environment

        def export(command):
            tokens = shlex.split(
                prepare_command(command, project=package, package=package)
            )
            assert tokens[:2] == ["uv", "export"]
            subprocess.run(  # noqa: S603 — pinned uv, reviewed export-only arguments
                [uv, *tokens[1:]],
                cwd=package,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            path = package / tokens[tokens.index("--output-file") + 1]
            assert "--hash=sha256:" in path.read_text(encoding="utf-8")
            return path.resolve()

        build_file = export(selected.before_build)
        flags = get_build_frontend_extra_flags(selected.build_frontend, 0, "")
        consumed = package / flags[flags.index("--build-constraints") + 1]
        assert consumed.resolve() == build_file
        assert consumed.is_file()
        assert "--require-hashes" in flags
        backend_export, backend_install, test_export, install = (
            selected.before_test.split(" && ")
        )
        backend_export_tokens = shlex.split(
            prepare_command(backend_export, project=package, package=package)
        )
        assert (
            backend_export_tokens[backend_export_tokens.index("--only-group") + 1]
            == "backend-build"
        )
        backend_file = export(backend_export)
        backend_install_tokens = shlex.split(
            prepare_command(backend_install, project=package, package=package)
        )
        assert backend_install_tokens[:3] == ["uv", "pip", "install"]
        assert {"--no-deps", "--require-hashes"} <= set(backend_install_tokens)
        assert (
            backend_install_tokens[backend_install_tokens.index("--only-binary") + 1]
            == ":all:"
        )
        backend_consumed = (
            package
            / backend_install_tokens[backend_install_tokens.index("--requirements") + 1]
        )
        assert backend_consumed.resolve() == backend_file
        assert backend_consumed.is_file()
        test_file = export(test_export)
        install_tokens = shlex.split(
            prepare_command(install, project=package, package=package)
        )
        assert install_tokens[:3] == ["uv", "pip", "install"]
        test_consumed = (
            package / install_tokens[install_tokens.index("--requirements") + 1]
        )
        assert test_consumed.resolve() == test_file
        assert test_consumed.is_file()
        assert {"--no-deps", "--require-hashes", "--require-build-hashes"} <= set(
            install_tokens
        )
        test_build_consumed = (
            package / install_tokens[install_tokens.index("--build-constraints") + 1]
        )
        assert test_build_consumed.resolve() == backend_file
        assert test_build_consumed.is_file()
        observed.append(identifier)

    monkeypatch.setattr(builder.ALL_PLATFORM_MODULES[platform], "build", inspect_build)
    if absolute:
        builder.main_inner(builder.GlobalOptions())
        assert observed == [identifier]
    else:
        with pytest.raises(ConfigurationError, match="constraints file not found"):
            builder.main_inner(builder.GlobalOptions())
        assert observed == []
