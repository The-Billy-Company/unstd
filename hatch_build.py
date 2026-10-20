"""Optional CPython stable-ABI build; the base package stays dependency free."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import sysconfig
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from setuptools import Distribution, Extension
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_ext import build_ext
from setuptools.errors import BaseError, CCompilerError


class CustomBuildHook(BuildHookInterface):
    """Build the JSON proof in C when a compiler and the limited API exist."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Add only a successfully built extension, or keep a portable wheel."""
        if sys.implementation.name != "cpython" or sysconfig.get_config_var(
            "Py_GIL_DISABLED"
        ):
            return
        self._temporary = TemporaryDirectory(prefix="unstd-native-")
        temporary = Path(self._temporary.name)
        extension = Extension(
            "unstd.serde._json_native",
            [str(Path(self.root) / "src/unstd/serde/_json_native.c")],
            define_macros=[("Py_LIMITED_API", "0x030C0000")],
            py_limited_api=True,
            optional=os.environ.get("CIBUILDWHEEL") != "1",
        )
        distribution = Distribution(
            {"name": "unstd-native", "ext_modules": [extension]}
        )
        command = build_ext(distribution)
        command.build_lib = str(temporary / "lib")
        command.build_temp = str(temporary / "temp")
        command.ensure_finalized()
        try:
            command.run()
        except (BaseError, CCompilerError, OSError) as error:
            if not extension.optional:
                raise
            self.app.display_warning(f"Native JSON build unavailable: {error}")
        output = Path(command.get_ext_fullpath(extension.name))
        target = Path(self.root) / "src/unstd/serde" / output.name
        if not output.is_file():
            if version == "editable":
                target.unlink(missing_ok=True)
            return
        wheel = bdist_wheel(distribution)
        wheel.py_limited_api = "cp312"
        wheel.bdist_dir = command.build_lib
        wheel.ensure_finalized()
        build_data["pure_python"] = False
        build_data["tag"] = "-".join(wheel.get_tag())
        if version == "editable":
            # Overwriting a loaded Mach-O inode invalidates macOS code signatures.
            # Publish a fresh inode so existing processes retain their mapping.
            with NamedTemporaryFile(dir=target.parent, delete=False) as staged:
                staged.write(output.read_bytes())
            staged_path = Path(staged.name)
            try:
                staged_path.replace(target)
            finally:
                staged_path.unlink(missing_ok=True)
        else:
            build_data["force_include"][str(output)] = f"unstd/serde/{output.name}"

    def finalize(
        self, version: str, build_data: dict[str, Any], artifact_path: str
    ) -> None:
        """Remove compiler outputs after Hatch has copied the extension."""
        if hasattr(self, "_temporary"):
            self._temporary.cleanup()
