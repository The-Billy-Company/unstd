"""Run the pinned wheel builder with admitted native CPython providers.

cibuildwheel's macOS framework reuse and final Windows PSF 3.12 binary can
select security-outdated runtimes. Keep its complete build, repair, architecture
and test pipeline; replace standard macOS and Windows 3.12 CPython installers.
Linux containers register the same admitted provider at their existing prefix;
other Windows versions retain their normal provider.
"""

from __future__ import annotations

from functools import cache, wraps
import hashlib
from importlib.metadata import version
import inspect
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


_PROBE = """
import json, platform, runpy, struct, sys, sysconfig
report = runpy.run_path(sys.argv[1])["runtime_report"]()
report.update(implementation=sys.implementation.name, version_info=list(sys.version_info[:2]),
              machine=platform.machine(), bits=struct.calcsize("P") * 8,
              platform=sysconfig.get_platform(),
              soabi=sysconfig.get_config_var("SOABI"),
              gil_disabled=bool(sysconfig.get_config_var("Py_GIL_DISABLED")))
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(bool(report["errors"]))
"""

_COMPILER_KEYS = (
    "CC",
    "CXX",
    "CFLAGS",
    "CCSHARED",
    "LDSHARED",
    "LDCXXSHARED",
    "AR",
    "ARFLAGS",
    "LDFLAGS",
    "CPPFLAGS",
)
_COMPILER_PROBE = """
import json, pathlib, sys, sysconfig
print(json.dumps(dict(schema=1, executable=str(pathlib.Path(sys.executable).resolve()),
                     prefix=sys.prefix, version=list(sys.version_info[:2]),
                     soabi=sysconfig.get_config_var("SOABI"),
                     compiler={name: sysconfig.get_config_var(name) for name in sys.argv[1:]})))
"""


@cache
def _managed_python(
    minor: str, platform_tag: str | None = None, soabi: str | None = None
) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        msg = "the audited uv runtime provider is required"
        raise RuntimeError(msg)
    request = minor
    if sys.platform == "linux" and soabi is not None:
        prefix = f"cpython-{minor.replace('.', '')}-{platform.machine()}-linux-"
        libc = soabi.removeprefix(prefix)
        if not soabi.startswith(prefix) or libc not in {"gnu", "musl"}:
            msg = (
                f"review the container's native Python ABI before provisioning {soabi}"
            )
            raise RuntimeError(msg)
        request = f"cpython-{minor}-linux-{platform.machine()}-{libc}"
    subprocess.run(  # noqa: S603 - fixed provider and validated builder version
        [uv, "python", "install", "--managed-python", request], check=True, timeout=300
    )
    python = Path(
        subprocess.check_output(  # noqa: S603
            [uv, "python", "find", "--managed-python", "--no-project", request],
            text=True,
            timeout=30,
        ).strip()
    )
    policy = Path(__file__).resolve().parents[1] / "runtime/tls.py"
    try:
        probe = subprocess.check_output(  # noqa: S603 - installed interpreter, fixed local policy
            [python, "-I", "-c", _PROBE, policy], text=True, timeout=30
        )
    except subprocess.CalledProcessError as error:
        print(error.output, end="", file=sys.stderr, flush=True)
        raise
    report = json.loads(probe)
    _validate_runtime(report, minor, platform_tag, soabi)
    print(probe, end="", flush=True)
    return python


def _validate_runtime(
    report: dict[str, object],
    minor: str,
    platform_tag: str | None,
    soabi: str | None = None,
) -> None:
    actual_platform = str(report["platform"])
    platform_matches = (
        actual_platform.replace("-", "_") == platform_tag
        if platform_tag is not None
        else actual_platform.endswith("-" + platform.machine())
    )
    if (
        report["errors"] != []
        or report["implementation"] != "cpython"
        or report["version_info"] != [int(part) for part in minor.split(".")]
        or str(report["machine"]).casefold() != platform.machine().casefold()
        or report["bits"] != struct.calcsize("P") * 8
        or report["gil_disabled"] is not False
        or (soabi is not None and report["soabi"] != soabi)
        or not platform_matches
    ):
        msg = f"managed runtime does not match the admitted native build: {report}"
        raise RuntimeError(msg)


def _contract(
    function: Callable[..., object],
    names: Sequence[str],
    defaults: tuple[object, ...] = (),
) -> None:
    parameters = tuple(inspect.signature(function).parameters.values())
    if (
        tuple(item.name for item in parameters) != tuple(names)
        or any(
            item.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            for item in parameters
        )
        or tuple(
            item.default
            for item in parameters
            if item.default is not inspect.Parameter.empty
        )
        != defaults
    ):
        msg = "cibuildwheel installer contract changed; review the runtime provider"
        raise RuntimeError(msg)


def _linux_runtime(identifier: str, directory: Path = Path("/opt/python")) -> None:
    """Admit and register the native provider in an ephemeral wheel container."""
    digits = identifier.removeprefix("cp")
    if (
        sys.platform != "linux"
        or not identifier.startswith("cp3")
        or not digits.isascii()
        or not digits.isdecimal()
        or len(digits) < 2
    ):
        msg = "the Linux provider requires a standard CPython build identifier"
        raise RuntimeError(msg)
    minor = f"{digits[0]}.{int(digits[1:])}"
    link = directory / f"{identifier}-{identifier}"
    if not link.is_symlink() or not link.is_dir():
        msg = f"review the container interpreter layout before replacing {link}"
        raise RuntimeError(msg)
    profile = json.loads(
        subprocess.check_output(  # noqa: S603 - existing builder interpreter
            [link / "bin/python", "-I", "-S", "-c", _COMPILER_PROBE, *_COMPILER_KEYS],
            text=True,
            timeout=30,
        )
    )
    python = _managed_python(minor, f"linux_{platform.machine()}", profile["soabi"])
    prefix = python.parent.parent
    if not (prefix / "bin/python").samefile(python):
        msg = f"review the managed interpreter layout before registering {python}"
        raise RuntimeError(msg)
    profile_path = Path(os.environ["UNSTD_WHEEL_COMPILER_CONFIG"])
    if not profile_path.is_absolute():
        msg = "the wheel compiler profile requires an absolute path"
        raise RuntimeError(msg)
    payload = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    profile_path.write_bytes(payload)
    print(
        json.dumps(
            {
                "compiler_profile": profile,
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            indent=2,
        ),
        flush=True,
    )
    # cibuildwheel uses this same prefix for compilation, repair and wheel tests.
    # Replace only its symlink; retain the container's original interpreter tree.
    with TemporaryDirectory(dir=directory) as temporary:
        replacement = Path(temporary) / link.name
        replacement.symlink_to(prefix, target_is_directory=True)
        replacement.replace(link)
    print(f"Admitted {link} -> {prefix}", flush=True)


def _configure() -> None:
    if version("cibuildwheel") != "4.3.1":
        msg = "review the managed-runtime adapter before changing cibuildwheel 4.3.1"
        raise RuntimeError(msg)
    if sys.platform == "darwin":
        from cibuildwheel.platforms import macos

        original = macos.install_cpython
        _contract(original, ("_tmp", "version", "url", "free_threading", "sha256"))

        @wraps(original)
        def install_macos(
            _tmp: Path, version: str, url: str, free_threading: bool, sha256: str
        ) -> Path:
            if not free_threading:
                return _managed_python(version)
            return original(_tmp, version, url, free_threading, sha256)

        macos.install_cpython = install_macos
    elif sys.platform == "win32":
        from cibuildwheel.platforms import windows

        original_windows = windows.install_cpython
        _contract(original_windows, ("configuration", "arch"), (None,))

        @wraps(original_windows)
        def install_windows(configuration, arch: str | None = None) -> Path:
            minor = ".".join(configuration.version.split(".")[:2])
            if minor == "3.12" and "t-" not in configuration.identifier:
                requested = windows.get_nuget_args(
                    configuration.version, arch or configuration.arch, False, Path()
                )[0]
                native = windows.get_nuget_args(
                    configuration.version, platform.machine(), False, Path()
                )[0]
                if requested != native:
                    msg = "the managed 3.12 provider requires a native architecture runner"
                    raise RuntimeError(msg)
                return _managed_python(
                    minor, configuration.identifier.partition("-")[2]
                )
            return original_windows(configuration, arch)

        windows.install_cpython = install_windows


def main() -> None:
    """Keep cibuildwheel's CLI and all artifact admission checks intact."""
    if sys.argv[1:2] == ["--linux-runtime"]:
        if len(sys.argv) != 3:
            msg = "--linux-runtime requires one CPython build identifier"
            raise RuntimeError(msg)
        _linux_runtime(sys.argv[2])
        return
    from cibuildwheel.__main__ import main as build_wheels

    _configure()
    build_wheels()


if __name__ == "__main__":
    main()
