"""Optional CPython native build; the base package stays dependency free."""

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


_NATIVE_MODULES = (
    "unstd.serde._json_native",
    "unstd.clone._clone_native",
    "unstd.text._text_native",
)


class CustomBuildHook(BuildHookInterface):
    """Build optional acceleration for the selected CPython interpreter."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Add only a successfully built extension, or keep a portable wheel."""
        if sys.implementation.name != "cpython" or sysconfig.get_config_var(
            "Py_GIL_DISABLED"
        ):
            return
        self._temporary = TemporaryDirectory(prefix="unstd-native-")
        temporary = Path(self._temporary.name)
        for index, module in enumerate(_NATIVE_MODULES):
            self._build_module(module, temporary / str(index), version, build_data)

    def _build_module(
        self,
        module: str,
        temporary: Path,
        version: str,
        build_data: dict[str, Any],
    ) -> None:
        """One compiler failure leaves every other optional module buildable."""
        relative = Path(*module.split("."))
        extension = Extension(
            module,
            [str(Path(self.root) / "src" / relative.with_suffix(".c"))],
            optional=os.environ.get("CIBUILDWHEEL") != "1",
            # Jaro preserves Python's separate IEEE double operations exactly.
            # These trailing compiler arguments override caller fast-math/FMA
            # options rather than silently changing the public score's bits.
            extra_compile_args=(
                ["/fp:strict"]
                if os.name == "nt"
                else ["-fno-fast-math", "-ffp-contract=off"]
            )
            if module == "unstd.text._text_native"
            else [],
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
            self.app.display_warning(f"Native build unavailable for {module}: {error}")
        output = Path(command.get_ext_fullpath(extension.name))
        target = Path(self.root) / "src" / relative.parent / output.name
        if not output.is_file():
            if version == "editable":
                self._clear_native(target.parent, relative.name)
            return
        wheel = bdist_wheel(distribution)
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
            self._clear_native(target.parent, relative.name, keep=target)
        else:
            build_data["force_include"][str(output)] = (
                relative.parent / output.name
            ).as_posix()

    def _clear_native(
        self, directory: Path, name: str, *, keep: Path | None = None
    ) -> None:
        """Remove stale generated modules, including earlier wheel ABIs."""
        for native in directory.glob(f"{name}.*"):
            if native.suffix in {".so", ".pyd"} and native != keep:
                native.unlink(missing_ok=True)

    def finalize(
        self, version: str, build_data: dict[str, Any], artifact_path: str
    ) -> None:
        """Remove compiler outputs after Hatch has copied the extension."""
        if hasattr(self, "_temporary"):
            self._temporary.cleanup()
