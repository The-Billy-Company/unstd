"""Run the pinned wheel builder with an admitted native CPython 3.12 provider.

cibuildwheel's final PSF 3.12 binary is security-outdated. Keep its complete
build, repair, architecture and test pipeline; replace only that installer on
macOS and Windows. Other versions and platforms retain their normal provider.
"""

from __future__ import annotations

from functools import cache, wraps
from importlib.metadata import version
import inspect
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


_PROBE = """
import json, platform, runpy, struct, sys, sysconfig
report = runpy.run_path(sys.argv[1])["runtime_report"]()
report.update(implementation=sys.implementation.name, version_info=list(sys.version_info[:2]),
              machine=platform.machine(), bits=struct.calcsize("P") * 8,
              platform=sysconfig.get_platform())
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(bool(report["errors"]))
"""


@cache
def _managed_python(minor: str, platform_tag: str | None = None) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        msg = "the audited uv runtime provider is required"
        raise RuntimeError(msg)
    subprocess.run(  # noqa: S603 - fixed provider and validated builder version
        [uv, "python", "install", "--managed-python", minor], check=True, timeout=300
    )
    python = Path(
        subprocess.check_output(  # noqa: S603
            [uv, "python", "find", "--managed-python", "--no-project", minor],
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
    _validate_runtime(report, minor, platform_tag)
    print(probe, end="", flush=True)
    return python


def _validate_runtime(
    report: dict[str, object], minor: str, platform_tag: str | None
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
            if version == "3.12" and not free_threading:
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
    from cibuildwheel.__main__ import main as build_wheels

    _configure()
    build_wheels()


if __name__ == "__main__":
    main()
