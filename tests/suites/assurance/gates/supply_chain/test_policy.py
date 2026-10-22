"""Dependency inputs fail closed at artifact, registry and resolver boundaries."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
from typing import TYPE_CHECKING
from urllib import request

import pytest

from tests.support.anchors import REPO_ROOT
from tests.tooling import supply_chain


if TYPE_CHECKING:
    from pathlib import Path


_DIGEST = "a" * 64
_WHEEL = (
    'wheels = [{url = "https://files.pythonhosted.org/packages/ab/backend.whl", '
    f'hash = "sha256:{_DIGEST}", size = 100}}]\n'
)
_MANIFEST = """[project]
name = "example"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = ["backend>=1"]
[project.optional-dependencies]
fast = ["backend>=1"]
[dependency-groups]
build = ["backend>=1"]
[build-system]
requires = ["backend>=1"]
build-backend = "backend"
[tool.uv]
exclude-newer = "P2D"
[tool.uv.exclude-newer-package]
irregex = false
certifi = "P1D"
"""
_LOCK = (
    """version = 1
revision = 5
requires-python = ">=3.12"
[options]
exclude-newer-span = "P2D"
[options.exclude-newer-package]
irregex = false
certifi = {timestamp = "0001-01-01T00:00:00Z", span = "P1D"}
[[package]]
name = "example"
version = "1.0.0"
source = {editable = "."}
dependencies = [{name = "backend"}]
[package.optional-dependencies]
fast = [{name = "backend"}]
[package.dev-dependencies]
build = [{name = "backend"}]
[[package]]
name = "backend"
version = "1.2.0"
source = {registry = "https://pypi.org/simple"}
"""
    + _WHEEL
)
_CARGO = """[package]
name = "native-example"
version = "0.1.0"
publish = false
"""
_CARGO_LOCK = """version = 3
[[package]]
name = "native-example"
version = "0.1.0"
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(_MANIFEST)
    (tmp_path / "uv.lock").write_text(_LOCK)
    native = tmp_path / "native/text"
    native.mkdir(parents=True)
    (native / "Cargo.toml").write_text(_CARGO)
    (native / "Cargo.lock").write_text(_CARGO_LOCK)
    return tmp_path


def _replace(root: Path, file: str, old: str, new: str) -> None:
    path = root / file
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_repository_lockfiles_pass_without_imports_network_or_subprocesses(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("offline policy attempted network access or process execution")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(request, "urlopen", forbidden)
    summary = supply_chain.check_policy(REPO_ROOT)
    assert summary["python_packages"] > 1
    assert summary["python_artifacts"] >= summary["python_packages"] - 1
    assert summary["rust_packages"] == len(
        list((REPO_ROOT / "native").rglob("Cargo.toml"))
    )
    assert summary["rust_external_packages"] == 0


def test_minimal_policy_counts_artifacts_and_accepts_dependency_floor_options(repo):
    _replace(
        repo, "uv.lock", "[options]\n", '[options]\nresolution-mode = "lowest-direct"\n'
    )
    assert supply_chain.check_policy(repo) == {
        "python_packages": 2,
        "python_artifacts": 1,
        "rust_packages": 1,
        "rust_external_packages": 0,
    }


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        (
            'registry = "https://pypi.org/simple"',
            'registry = "https://mirror.example/simple"',
            "source",
        ),
        (
            'registry = "https://pypi.org/simple"',
            'url = "https://example.com/backend.whl"',
            "source",
        ),
        (
            'registry = "https://pypi.org/simple"',
            'git = "https://example.com/backend.git"',
            "source",
        ),
        ('registry = "https://pypi.org/simple"', 'path = "../backend"', "source"),
        ('registry = "https://pypi.org/simple"', 'editable = "."', "source"),
        ('editable = "."', 'editable = "../elsewhere"', "source"),
        ('version = "1.0.0"', 'version = "2.0.0"', "root version"),
        ('requires-python = ">=3.12"', 'requires-python = ">=3.13"', "requires-python"),
        ('exclude-newer-span = "P2D"', 'exclude-newer-span = "P1D"', "quarantine"),
        ('span = "P1D"', 'span = "P2D"', "exemptions"),
        ('{name = "backend"}', '{name = "missing"}', "unresolved"),
        (
            '{name = "backend"}',
            '{name = "backend", source = {git = "https://example.com/repo"}}',
            "unapproved source",
        ),
        (_WHEEL, "", "no hashed artifacts"),
        ('hash = "sha256:' + _DIGEST + '"', 'hash = "md5:' + _DIGEST + '"', "SHA256"),
        (_DIGEST, "a" * 63, "SHA256"),
        (_DIGEST, "A" * 64, "SHA256"),
        ("size = 100", "size = 0", "positive integer"),
        ("size = 100", "size = true", "positive integer"),
        ("version = 1\n", "version = true\n", "lockfile version"),
        ('name = "backend"\n', 'name = "../backend"\n', "package name"),
    ],
)
def test_tampered_uv_lock_is_rejected(repo, old, new, error):
    _replace(repo, "uv.lock", old, new)
    with pytest.raises(supply_chain.SupplyChainError, match=re.escape(error)):
        supply_chain.check_policy(repo)


@pytest.mark.parametrize(
    "url",
    [
        "http://files.pythonhosted.org/packages/a.whl",
        "https://files.pythonhosted.org.evil.example/packages/a.whl",
        "https://files.pythonhosted.org@evil.example/packages/a.whl",
        "https://user:secret@files.pythonhosted.org/packages/a.whl",
        "https://files.pythonhosted.org:443/packages/a.whl",
        "https://files.pythonhosted.org/packages/a.whl?redirect=evil",
        "https://files.pythonhosted.org/packages/a.whl#ignored",
        "https://files.pythonhosted.org/elsewhere/a.whl",
        "https://files.pythonhosted.org/packages/a.tar.gz",
    ],
)
def test_artifact_urls_cannot_cross_the_approved_download_boundary(repo, url):
    _replace(
        repo, "uv.lock", "https://files.pythonhosted.org/packages/ab/backend.whl", url
    )
    with pytest.raises(supply_chain.SupplyChainError, match="url"):
        supply_chain.check_policy(repo)


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ('exclude-newer = "P2D"', 'exclude-newer = "P1D"', "quarantine"),
        ("irregex = false", "irregex = false\nbackend = false", "exceptions"),
        ('certifi = "P1D"', "certifi = false", "exceptions"),
        (
            'dependencies = ["backend>=1"]',
            'dependencies = ["backend @ https://evil.example/b.whl"]',
            "registry names",
        ),
        (
            'fast = ["backend>=1"]',
            'fast = ["backend @ file:///tmp/b.whl"]',
            "registry names",
        ),
        (
            'build = ["backend>=1"]',
            'build = ["backend @ git+https://example.com/repo"]',
            "registry names",
        ),
        (
            'requires = ["backend>=1"]',
            'requires = ["backend @ https://evil.example/b.whl"]',
            "registry names",
        ),
        (
            'dependencies = ["backend>=1"]',
            'dependencies = ["unexpected>=1"]',
            "direct dependency names",
        ),
        (
            'exclude-newer = "P2D"',
            'exclude-newer = "P2D"\nindex-url = "https://evil.example/simple"',
            "source overrides",
        ),
        (
            'exclude-newer = "P2D"',
            'exclude-newer = "P2D"\nallow-insecure-host = ["pypi.org"]',
            "source overrides",
        ),
    ],
)
def test_manifest_cannot_bypass_source_or_quarantine_policy(repo, old, new, error):
    _replace(repo, "pyproject.toml", old, new)
    with pytest.raises(supply_chain.SupplyChainError, match=error):
        supply_chain.check_policy(repo)


def test_standalone_uv_configuration_cannot_override_the_reviewed_manifest(repo):
    (repo / "uv.toml").write_text('index-url = "https://evil.example/simple"\n')
    with pytest.raises(
        supply_chain.SupplyChainError, match="standalone resolver overrides"
    ):
        supply_chain.check_policy(repo)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            'requires = ["backend>=1"]',
            'requires = ["backend>=1", "unlocked-builder>=1"]',
        ),
        ('build = ["backend>=1"]', "build = []"),
        ('build = ["backend>=1"]', 'build = ["backend"]'),
        ('build = ["backend>=1"]', 'build = ["backend>=2"]'),
        (
            'build = ["backend>=1"]',
            "build = [\"backend>=1 ; sys_platform == 'win32'\"]",
        ),
        ('requires = ["backend>=1"]', "requires = []"),
    ],
)
def test_build_backend_requirements_cannot_escape_or_weaken_the_audited_build_group(
    repo, old, new
):
    _replace(repo, "pyproject.toml", old, new)
    with pytest.raises(
        supply_chain.SupplyChainError, match="must exactly match the audited"
    ):
        supply_chain.check_policy(repo)


def test_build_group_and_native_manifest_are_required_inputs(repo):
    _replace(repo, "pyproject.toml", 'build = ["backend>=1"]\n', "")
    with pytest.raises(
        supply_chain.SupplyChainError, match=r"dependency-groups\.build"
    ):
        supply_chain.check_policy(repo)
    (repo / "pyproject.toml").write_text(_MANIFEST)
    (repo / "native/text/Cargo.toml").unlink()
    with pytest.raises(
        supply_chain.SupplyChainError, match="native crate manifest is missing"
    ):
        supply_chain.check_policy(repo)


def test_python_only_checkout_needs_no_unrelated_native_experiment(repo):
    (repo / "native/text/Cargo.lock").unlink()
    (repo / "native/text/Cargo.toml").unlink()
    assert supply_chain.check_policy(repo)["rust_packages"] == 0


def test_uv_relative_quarantine_exception_requires_exact_locked_timestamp_and_span(
    repo,
):
    _replace(
        repo,
        "uv.lock",
        'timestamp = "0001-01-01T00:00:00Z"',
        'timestamp = "2026-10-21T00:00:00Z"',
    )
    with pytest.raises(supply_chain.SupplyChainError, match="exemptions"):
        supply_chain.check_policy(repo)


def test_duplicate_package_and_missing_editable_root_cannot_claim_valid_lock(repo):
    with (repo / "uv.lock").open("a") as stream:
        stream.write(
            '\n[[package]]\nname = "backend"\nversion = "1.2.0"\nsource = {registry = "https://pypi.org/simple"}\n'
            + _WHEEL
        )
    with pytest.raises(supply_chain.SupplyChainError, match="duplicate"):
        supply_chain.check_policy(repo)
    (repo / "uv.lock").write_text(
        _LOCK.replace('editable = "."', 'registry = "https://pypi.org/simple"').replace(
            'source = {registry = "https://pypi.org/simple"}\ndependencies',
            'source = {registry = "https://pypi.org/simple"}\n'
            + _WHEEL
            + "dependencies",
        )
    )
    with pytest.raises(supply_chain.SupplyChainError, match="exactly one editable"):
        supply_chain.check_policy(repo)


@pytest.mark.parametrize(
    "section",
    [
        "dependencies",
        "dev-dependencies",
        "build-dependencies",
        "target.'cfg(unix)'.dependencies",
    ],
)
@pytest.mark.parametrize(
    "spec", ['"1"', '{git = "https://example.com/repo"}', '{path = "../other"}']
)
def test_new_rust_dependencies_require_their_own_audited_lane(repo, section, spec):
    with (repo / "native/text/Cargo.toml").open("a") as stream:
        stream.write(f"\n[{section}]\nexternal = {spec}\n")
    with pytest.raises(
        supply_chain.SupplyChainError,
        match="external Rust dependencies require an audited lane",
    ):
        supply_chain.check_policy(repo)


@pytest.mark.parametrize(
    "mutation",
    [
        'source = "registry+https://github.com/rust-lang/crates.io-index"\nchecksum = "'
        + _DIGEST
        + '"\n',
        'source = "git+https://example.com/repo#1234"\n',
    ],
)
def test_external_crate_lock_entries_cannot_escape_the_dependency_free_contract(
    repo, mutation
):
    with (repo / "native/text/Cargo.lock").open("a") as stream:
        stream.write('\n[[package]]\nname = "external"\nversion = "1.0.0"\n' + mutation)
    with pytest.raises(
        supply_chain.SupplyChainError,
        match="external Rust dependencies require an audited lane",
    ):
        supply_chain.check_policy(repo)


@pytest.mark.parametrize(
    "location",
    [
        ".cargo/config",
        ".cargo/config.toml",
        "native/.cargo/config.toml",
        "native/text/.cargo/config.toml",
    ],
)
def test_cargo_source_replacement_is_rejected_at_every_repository_ancestor(
    repo, location
):
    path = repo / location
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[source.crates-io]\nreplace-with = "mirror"\n')
    with pytest.raises(supply_chain.SupplyChainError, match="source overrides"):
        supply_chain.check_policy(repo)


@pytest.mark.parametrize(
    ("file", "text", "error"),
    [
        ("uv.lock", "[invalid", "cannot read uv.lock"),
        (
            "uv.lock",
            'version = 1\nrequires-python = ">=3.12"\noptions = "invalid"',
            "expected a TOML table",
        ),
        ("native/text/Cargo.lock", "version = 3\npackage = []", "exactly one local"),
        (
            "native/text/Cargo.lock",
            _CARGO_LOCK.replace("0.1.0", "0.2.0"),
            "unapproved local",
        ),
        (
            "native/text/Cargo.toml",
            _CARGO + '[patch.crates-io]\nevil = "1"\n',
            "patch/replace",
        ),
    ],
)
def test_malformed_or_inconsistent_inputs_are_actionable_failures(
    repo, file, text, error
):
    (repo / file).write_text(text)
    with pytest.raises(supply_chain.SupplyChainError, match=error):
        supply_chain.check_policy(repo)


def test_missing_lock_and_symlink_escape_fail_closed(repo, tmp_path):
    lock = repo / "uv.lock"
    lock.unlink()
    with pytest.raises(supply_chain.SupplyChainError, match=r"cannot read uv\.lock"):
        supply_chain.check_policy(repo)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.lock"
    outside.write_text(_LOCK)
    lock.symlink_to(outside)
    with pytest.raises(supply_chain.SupplyChainError, match="escapes the repository"):
        supply_chain.check_policy(repo)


def test_sbom_contains_every_locked_component_hash_and_dependency_deterministically(
    repo,
):
    expected = supply_chain.build_sbom(repo)
    assert expected == supply_chain.build_sbom(repo)
    assert expected["bomFormat"] == "CycloneDX"
    assert expected["specVersion"] == "1.6"
    components = expected["components"]
    assert [component["purl"] for component in components] == [
        "pkg:cargo/native-example@0.1.0",
        "pkg:pypi/backend@1.2.0",
        "pkg:pypi/example@1.0.0",
    ]
    assert components[1]["hashes"] == [{"alg": "SHA-256", "content": _DIGEST}]
    assert {row["ref"]: row["dependsOn"] for row in expected["dependencies"]} == {
        "pkg:cargo/native-example@0.1.0": [],
        "pkg:pypi/backend@1.2.0": [],
        "pkg:pypi/example@1.0.0": ["pkg:pypi/backend@1.2.0"],
    }
    # Hash metadata is deduplicated without losing counts of artifact entries.
    _replace(
        repo,
        "uv.lock",
        _WHEEL,
        _WHEEL.replace(
            "}]",
            '}, {url = "https://files.pythonhosted.org/packages/ab/other.whl", hash = "sha256:'
            + _DIGEST
            + '", size = 50}]',
        ),
    )
    assert supply_chain.check_policy(repo)["python_artifacts"] == 2
    assert supply_chain.build_sbom(repo) == expected


def test_cli_json_and_saved_sbom_match_the_library_and_invalid_policy_creates_no_output(
    repo, capsys
):
    assert supply_chain.main(["check", "--json"], repo_root=repo) == 0
    assert json.loads(capsys.readouterr().out) == supply_chain.check_policy(repo)
    path = repo / "reports/sbom.json"
    assert supply_chain.main(["sbom", "--output", str(path)], repo_root=repo) == 0
    assert json.loads(path.read_text()) == supply_chain.build_sbom(repo)
    encoded = path.read_bytes()
    assert supply_chain.main(["sbom", "--output", str(path)], repo_root=repo) == 0
    assert path.read_bytes() == encoded
    (repo / "uv.lock").unlink()
    bad_output = repo / "invalid/sbom.json"
    assert supply_chain.main(["sbom", "--output", str(bad_output)], repo_root=repo) == 1
    assert "cannot read uv.lock" in capsys.readouterr().err
    assert not bad_output.parent.exists()


def test_real_cli_runs_on_an_explicit_repository_without_installing_tools(repo):
    result = subprocess.run(  # noqa: S603 — fixed interpreter/module, literal arguments
        [
            sys.executable,
            "-m",
            "tests.tooling.supply_chain",
            "--repo-root",
            str(repo),
            "check",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["python_artifacts"] == 1
