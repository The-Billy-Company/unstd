"""Bounded, offline artifact builds and pristine consumer interpreters.

The pytest environment supplies the builder and type checker. It is never the
consumer: wheels are installed without dependencies into unseeded virtualenvs,
and every product probe runs in a new isolated Python process outside the repo.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from typing import TYPE_CHECKING

from packaging.markers import Marker
from packaging.utils import canonicalize_name

from tests.support.anchors import REPO_ROOT


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


COMMAND_TIMEOUT = 90
CONSUMER_TIMEOUT = 30


def clean_environment() -> dict[str, str]:
    """Only process-launch/platform variables survive into a build or consumer."""
    keep = {
        "HOME",
        "PATH",
        "SYSTEMROOT",
        "SystemRoot",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        # Reuse a caller-selected cache; never inherit resolver/install policy.
        "UV_CACHE_DIR",
    }
    env = {key: value for key, value in os.environ.items() if key in keep}
    env.update(
        {
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_NO_PROGRESS": "1",
            "UV_OFFLINE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def checked_command(
    argv: Sequence[str | Path],
    *,
    cwd: Path,
    timeout: int = COMMAND_TIMEOUT,
    env: Mapping[str, str] | None = None,
    expected_code: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Execute explicit argv with a deadline and retain all failure evidence."""
    command = [str(part) for part in argv]
    result = subprocess.run(  # noqa: S603 — explicit argv, no shell, fixed deadline
        command,
        cwd=cwd,
        env=dict(env) if env is not None else clean_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    assert result.returncode == expected_code, (
        f"command {command!r} exited {result.returncode}, expected {expected_code}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def uv_executable() -> Path:
    """The existing uv installation; required tooling fails loudly if missing."""
    executable = shutil.which("uv")
    assert executable is not None, "packaging tests require uv on PATH"
    return Path(executable)


@dataclass(frozen=True)
class Artifacts:
    """Both build routes plus the source snapshot used as an archive oracle."""

    root: Path
    source: Path
    wheel: Path
    sdist: Path
    sdist_wheel: Path
    package_files: dict[str, bytes]
    project: dict[str, object]


def build_artifacts(root: Path) -> Artifacts:
    """Build an isolated wheel/sdist, then independently build that sdist offline."""
    assert not root.resolve().is_relative_to(REPO_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    source = root / "checkout"
    source.mkdir()
    # Read and build one frozen working tree, including untracked/new modules.
    # Concurrent development cannot change a fixture between its oracle read,
    # wheel build, sdist build and sdist rebuild. Local environments and caches
    # are never release inputs.
    excluded = shutil.ignore_patterns(
        ".git",
        ".venv",
        ".local",
        "__pycache__",
        "*.pyc",
        "*.so",
        "*.pyd",
        "build",
        ".pytest_cache",
        ".hypothesis",
        ".mypy_cache",
        ".ruff_cache",
    )
    for name in (
        "src",
        "pyproject.toml",
        "hatch_build.py",
        "README.md",
        "LICENSE",
        "NOTICE",
        "CHANGELOG.md",
        "bench",
        "tests",
        "changelog.d",
        "uv.lock",
        ".gitignore",
        ".github",
    ):
        original = REPO_ROOT / name
        if original.is_dir():
            shutil.copytree(original, source / name, ignore=excluded)
        elif original.is_file():
            shutil.copy2(original, source / name)
    output = root / "source"
    rebuilt = root / "from-sdist"
    package_files = {
        path.relative_to(source / "src").as_posix(): path.read_bytes()
        for path in (source / "src/unstd").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    with (source / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    uv = uv_executable()
    common = [
        uv,
        "build",
        "--offline",
        "--no-config",
        "--no-sources",
        "--no-python-downloads",
        "--python",
        sys.executable,
        "--exclude-newer",
        "P2D",
    ]
    checked_command(
        [*common, source, "--sdist", "--wheel", "--out-dir", output], cwd=root
    )
    wheels = list(output.glob("*.whl"))
    sdists = list(output.glob("*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, sorted(output.iterdir())
    checked_command([*common, sdists[0], "--wheel", "--out-dir", rebuilt], cwd=root)
    rebuilt_wheels = list(rebuilt.glob("*.whl"))
    assert len(rebuilt_wheels) == 1, sorted(rebuilt.iterdir())
    return Artifacts(
        root, source, wheels[0], sdists[0], rebuilt_wheels[0], package_files, project
    )


@dataclass(frozen=True)
class Consumer:
    """An unseeded virtualenv containing only an installed unstd wheel."""

    root: Path
    python: Path
    wheel: Path

    def run(self, code: str) -> subprocess.CompletedProcess[str]:
        """A new Python process with cwd, import search and environment isolated."""
        return checked_command(
            [self.python, "-I", "-c", code, REPO_ROOT, self.root],
            cwd=self.root,
            timeout=CONSUMER_TIMEOUT,
        )


def create_consumer(root: Path, wheel: Path) -> Consumer:
    """Install a local wheel offline without pip, setuptools or backend extras."""
    assert not root.resolve().is_relative_to(REPO_ROOT)
    root.mkdir(parents=True)
    uv = uv_executable()
    venv = root / "venv"
    checked_command(
        [
            uv,
            "venv",
            "--offline",
            "--no-config",
            "--no-python-downloads",
            "--python",
            sys.executable,
            venv,
        ],
        cwd=root,
    )
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    checked_command(
        [
            uv,
            "pip",
            "install",
            "--offline",
            "--no-config",
            "--no-deps",
            "--link-mode",
            "copy",
            "--python",
            python,
            wheel,
        ],
        cwd=root,
    )
    return Consumer(root, python, wheel)


def install_extra_closure(
    artifacts: Artifacts, consumer: Consumer, extra: str
) -> dict[str, str]:
    """Install one hash-pinned closure offline from its locked artifact URLs."""
    lockfile = consumer.root / f"pylock.{extra}.toml"
    lock_before = (artifacts.source / "uv.lock").read_bytes()
    lock_options = tomllib.loads(lock_before.decode("utf-8")).get("options", {})
    # uv sync --resolution lowest-direct writes a distinct lock. Preserve its
    # strategy when checking freshness rather than asking export to re-lock it
    # with the default highest strategy.
    resolution = lock_options.get("resolution-mode")
    resolver_args = ["--resolution", resolution] if resolution is not None else []
    uv = uv_executable()
    checked_command(
        [
            uv,
            "export",
            "--locked",
            "--offline",
            "--no-config",
            "--no-sources",
            *resolver_args,
            "--exclude-newer",
            "P2D",
            "--exclude-newer-package",
            "irregex=false",
            "--no-dev",
            "--no-default-groups",
            "--no-emit-project",
            "--extra",
            extra,
            "--format",
            "pylock.toml",
            "--no-annotate",
            "--no-header",
            "--output-file",
            lockfile,
        ],
        cwd=artifacts.source,
    )
    assert (artifacts.source / "uv.lock").read_bytes() == lock_before
    expected = {}
    packages = tomllib.loads(lockfile.read_text(encoding="utf-8")).get("packages", [])
    for package in packages:
        marker = package.get("marker")
        if marker is not None and not Marker(marker).evaluate():
            continue
        name = canonicalize_name(package["name"])
        assert name not in expected, package
        expected[name] = package["version"]
    if expected:
        checked_command(
            [
                uv,
                "pip",
                "install",
                "--offline",
                "--no-config",
                "--no-deps",
                "--require-hashes",
                "--exclude-newer",
                "P2D",
                "--link-mode",
                "copy",
                "--python",
                consumer.python,
                "--requirements",
                lockfile,
            ],
            cwd=consumer.root,
        )
    return expected


CONSUMER_PROVENANCE = """
import importlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import sys
import unstd

repo = Path(sys.argv[1]).resolve()
consumer = Path(sys.argv[2]).resolve()
assert sys.flags.isolated == 1
assert sys.prefix != sys.base_prefix
assert Path(sys.prefix).resolve().is_relative_to(consumer)
assert not Path.cwd().resolve().is_relative_to(repo)
assert all(not Path(path).resolve().is_relative_to(repo) for path in sys.path)
assert Path(unstd.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert not {'PYTHONPATH', 'PYTHONHOME', 'MYPYPATH', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'} & os.environ.keys()
"""

CONSUMER_AUDIT = (
    CONSUMER_PROVENANCE
    + """
assert {dist.metadata['Name'].lower() for dist in importlib.metadata.distributions()} == {'unstd'}
for module in ('orjson', 'msgspec', 'pybase64', 'uuid_utils', 'blake3', 'whenever',
               'google', 'pydantic', 'pydantic_settings', 'irgx', 'sortedcontainers',
               'immutables', 'numpy', 'rapidfuzz', 'soundfile', 'tomlkit', 'ruamel', 'zlib_ng'):
    assert importlib.util.find_spec(module) is None, module
"""
)
