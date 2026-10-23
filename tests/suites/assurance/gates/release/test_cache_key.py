"""Reuse compiled dependencies across releases without losing locked inputs."""

from __future__ import annotations

import re

import pytest

from tests.tooling.release.cache_key import cache_key, main


_PROJECT = """\
[build-system]
requires = ["hatchling", "setuptools>=77"]
build-backend = "hatchling.build"

[project]
name = "unstd"
version = "3.1.4"
requires-python = ">=3.12"

[project.optional-dependencies]
crypto = ["blake3>=1.0"]

[dependency-groups]
release-uv = ["uv==0.13.4"]
backend-build = ["setuptools>=77"]

[tool.uv]
exclude-newer = "P2D"

[tool.cibuildwheel]
build = "cp3{12,13,14}-*"
build-frontend = "uv"
"""

_LOCK = """\
version = 1
requires-python = ">=3.12"

[[package]]
name = "unstd"
version = "3.1.4"
source = { editable = "." }

[package.optional-dependencies]
crypto = [{ name = "blake3" }]

[package.dev-dependencies]
backend-build = [{ name = "setuptools" }]

[package.metadata.requires-dev]
backend-build = [{ name = "setuptools", specifier = ">=77" }]

[[package]]
name = "blake3"
version = "1.0.10"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/blake3.tar.gz", hash = "sha256:aaaa" }
wheels = [{ url = "https://files.pythonhosted.org/blake3.whl", hash = "sha256:bbbb" }]
"""


@pytest.fixture
def source(tmp_path):
    (tmp_path / "pyproject.toml").write_text(_PROJECT, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(_LOCK, encoding="utf-8")
    return tmp_path


def test_project_version_bumps_keep_compiled_dependencies_reusable(source):
    before = cache_key(source)
    for name in ("pyproject.toml", "uv.lock"):
        path = source / name
        path.write_text(path.read_text().replace('"3.1.4"', '"3.2.0"'))
    assert cache_key(source) == before


@pytest.mark.parametrize(
    ("filename", "old", "new"),
    [
        ("uv.lock", '"1.0.10"', '"1.0.11"'),
        ("uv.lock", "sha256:aaaa", "sha256:cccc"),
        ("uv.lock", "sha256:bbbb", "sha256:dddd"),
        ("uv.lock", "blake3.tar.gz", "blake3-reviewed.tar.gz"),
        ("uv.lock", '{ name = "blake3" }', '{ name = "blake3", extra = ["x"] }'),
        ("uv.lock", 'name = "setuptools"', 'name = "wheel"'),
        ("uv.lock", 'specifier = ">=77"', 'specifier = ">=78"'),
        ("pyproject.toml", "uv==0.13.4", "uv==0.13.5"),
        ("pyproject.toml", 'build-frontend = "uv"', 'build-frontend = "build"'),
        ("pyproject.toml", "setuptools>=77", "setuptools>=78"),
        ("pyproject.toml", 'requires-python = ">=3.12"', 'requires-python = ">=3.13"'),
        ("pyproject.toml", 'exclude-newer = "P2D"', 'exclude-newer = "P3D"'),
    ],
)
def test_dependency_build_and_artifact_changes_invalidate_cache(
    source, filename, old, new
):
    before = cache_key(source)
    path = source / filename
    original = path.read_text()
    assert old in original
    path.write_text(original.replace(old, new))
    assert cache_key(source) != before


def test_comments_and_toml_mapping_order_do_not_invalidate_dependencies(source):
    before = cache_key(source)
    for name in ("pyproject.toml", "uv.lock"):
        path = source / name
        text = path.read_text().replace(
            'name = "unstd"\nversion = "3.1.4"',
            'version = "3.1.4"\nname = "unstd"',
        )
        path.write_text(text + "\n# formatting-only change\n")
    assert cache_key(source) == before


def test_missing_editable_root_fails_before_emitting_a_reusable_key(source):
    (source / "uv.lock").write_text(_LOCK.replace('editable = "."', 'virtual = "."'))
    with pytest.raises(ValueError, match="exactly one editable root"):
        cache_key(source)


@pytest.mark.parametrize("explicit", [False, True], ids=["environment", "option"])
def test_cli_prints_digest_and_appends_named_github_output(
    source, tmp_path, monkeypatch, capsys, explicit
):
    output = tmp_path / "github output.txt"
    output.write_text("previous=value\n")
    arguments = ["--root", str(source)]
    if explicit:
        monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "unused-output"))
        arguments.extend(("--github-output", str(output)))
    else:
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    main(arguments)
    digest = capsys.readouterr().out.strip()
    assert re.fullmatch("[0-9a-f]{64}", digest)
    assert digest == cache_key(source)
    assert output.read_text() == f"previous=value\nkey={digest}\n"


def test_cli_can_print_digest_without_github_environment(source, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    main(["--root", str(source)])
    assert capsys.readouterr().out == cache_key(source) + "\n"
