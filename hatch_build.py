"""Optional CPython native build; the base package stays dependency free."""

from __future__ import annotations

import hashlib
from importlib.metadata import version as installed_version
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess  # nosec B404 # build tools require explicit compiler processes
import sys
import sysconfig
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from setuptools import Distribution, Extension
from setuptools._distutils.ccompiler import new_compiler
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_ext import build_ext
from setuptools.errors import BaseError, CCompilerError


_NATIVE_MODULES = (
    "unstd.serde._json_native",
    "unstd.clone._clone_native",
    "unstd.crypto._stream_native",
    "unstd.text._text_native",
    "unstd.compress._gzip_native",
    "unstd.containers._sorted_native",
)

_COMPILER_KEYS = {
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
}
_COMPILER_COMMANDS = """
import json, sys, sysconfig
sys.path.insert(0, sys.argv[1])
from importlib.metadata import version
from setuptools._distutils.ccompiler import new_compiler
from setuptools._distutils.sysconfig import customize_compiler
compiler = new_compiler(compiler="unix")
customize_compiler(compiler)
print(json.dumps(dict(version=list(sys.version_info[:2]), soabi=sysconfig.get_config_var("SOABI"),
                     prefix=sys.prefix,
                     compiler={name: sysconfig.get_config_var(name) for name in sys.argv[2:]},
                     setuptools=version("setuptools"),
                     commands={name: getattr(compiler, name) for name in compiler.executables})))
"""


def _compiler_commands() -> dict[str, list[str] | None] | None:
    """Keep the container compiler while the admitted runtime owns Python's SDK."""
    path = os.environ.get("UNSTD_WHEEL_COMPILER_CONFIG")
    if os.environ.get("CIBUILDWHEEL") != "1" or path is None:
        return None
    with Path(path).open("rb") as stream:
        payload = stream.read((1 << 16) + 1)
    profile = json.loads(payload)
    if (
        len(payload) > 1 << 16
        or not isinstance(profile, dict)
        or payload
        != json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
        or set(profile)
        != {"schema", "executable", "prefix", "version", "soabi", "compiler"}
        or type(profile["schema"]) is not int
        or profile["schema"] != 1
        or profile["version"] != list(sys.version_info[:2])
        or profile["soabi"] != sysconfig.get_config_var("SOABI")
        or not all(
            isinstance(profile[name], str) and Path(profile[name]).is_absolute()
            for name in ("executable", "prefix")
        )
        or not isinstance(profile["compiler"], dict)
        or set(profile["compiler"]) != _COMPILER_KEYS
        or any(
            value is not None and (not isinstance(value, str) or "\0" in value)
            for value in profile["compiler"].values()
        )
    ):
        message = (
            "invalid original wheel compiler profile; review the container provider"
        )
        raise ValueError(message)
    package_file = sys.modules["setuptools"].__file__
    if package_file is None:
        message = "the locked build dependency lacks a source location"
        raise RuntimeError(message)
    report = json.loads(
        subprocess.check_output(  # noqa: S603 # nosec B603 # retained local interpreter, locked build dependencies
            [
                profile["executable"],
                "-I",
                "-S",
                "-c",
                _COMPILER_COMMANDS,
                str(Path(package_file).parent.parent),
                *sorted(_COMPILER_KEYS),
            ],
            text=True,
            timeout=30,
        )
    )
    commands = report["commands"]
    mismatched = [
        name
        for name in ("version", "soabi", "prefix", "compiler")
        if report[name] != profile[name]
    ]
    if report["setuptools"] != installed_version("setuptools"):
        mismatched.append("setuptools")
    if (
        not isinstance(commands, dict)
        or set(commands) != set(new_compiler(compiler="unix").executables)
        or any(
            value is not None
            and (
                not isinstance(value, list)
                or any(
                    not isinstance(part, str)
                    or "\0" in part
                    or profile["prefix"] in part
                    for part in value
                )
            )
            for value in commands.values()
        )
    ):
        mismatched.append("commands")
    if mismatched:
        print(json.dumps({"wheel_compiler_mismatch": mismatched}), flush=True)
        message = "original wheel compiler commands disagree with the admitted build"
        raise ValueError(message)
    print(
        json.dumps(
            {
                "wheel_compiler": report,
                "profile_sha256": hashlib.sha256(payload).hexdigest(),
            },
            indent=2,
        ),
        flush=True,
    )
    return commands


class _ContainerBuildExt(build_ext):
    compiler_commands: dict[str, list[str] | None]

    def build_extensions(self) -> None:
        self.compiler.set_executables(
            **{
                name: None if value is None else value.copy()
                for name, value in self.compiler_commands.items()
            }
        )
        super().build_extensions()


class CustomBuildHook(BuildHookInterface):
    """Build optional acceleration for the selected CPython interpreter."""

    _compiler_commands: dict[str, list[str] | None] | None = None

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Add only a successfully built extension, or keep a portable wheel."""
        if sys.implementation.name != "cpython" or sysconfig.get_config_var(
            "Py_GIL_DISABLED"
        ):
            return
        self._compiler_commands = _compiler_commands()
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
        rust_text = (
            module == "unstd.text._text_native"
            and os.environ.get("UNSTD_TEXT_RUST") == "1"
        )
        gzip_buffers = module == "unstd.compress._gzip_native"
        # An explicitly reviewed archive is a build-only experiment. Nothing
        # probes for another inflater or loads Rust dependencies at runtime.
        zlib_rs = (
            self._zlib_rs_archive(temporary / "reviewed-zlib-rs")
            if gzip_buffers
            else None
        )
        source = (
            Path(self.root)
            / "native"
            / relative.parts[1]
            / ("rust" if rust_text else "src")
            / "adapter.c"
        )
        gzip_root = Path(self.root) / "native/compress/vendor/libdeflate"
        sources = [str(source)]
        if gzip_buffers:
            sources.extend(
                str(Path(self.root) / "native/compress/src" / name)
                for name in ("stream.c", "inflate.c")
            )
            sources.extend(
                str(gzip_root / name)
                for name in (
                    "lib/arm/cpu_features.c",
                    "lib/x86/cpu_features.c",
                    "lib/utils.c",
                    "lib/deflate_decompress.c",
                    "lib/crc32.c",
                    "lib/gzip_decompress.c",
                )
            )
            if sys.platform != "darwin" and zlib_rs is None:
                sources.extend(
                    str(Path(self.root) / "native/compress/vendor/zlib" / name)
                    for name in (
                        "inflate.c",
                        "inffast.c",
                        "inftrees.c",
                        "adler32.c",
                        "crc32.c",
                    )
                )
        extension = Extension(
            module,
            sources,
            include_dirs=[str(gzip_root), str(gzip_root.parent)]
            if gzip_buffers
            else [],
            libraries=["z"]
            if gzip_buffers and sys.platform == "darwin" and zlib_rs is None
            else [],
            # Supply the same checked Python RAW allocators for zlib's default
            # ABI hooks too; do not compile its unused unchecked malloc hook.
            define_macros=[("UNSTD_ZLIB_RS", None)]
            if zlib_rs is not None
            else [("Z_PREFIX", None), ("MY_ZCALLOC", None)]
            if gzip_buffers and sys.platform != "darwin"
            else [],
            extra_objects=[str(zlib_rs)] if zlib_rs is not None else [],
            extra_link_args=shlex.split(
                os.environ.get("UNSTD_GZIP_ZLIB_RS_LINK_ARGS", "")
            )
            if zlib_rs is not None
            else [],
            optional=os.environ.get("CIBUILDWHEEL") != "1" and zlib_rs is None,
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
        command = (
            _ContainerBuildExt if self._compiler_commands is not None else build_ext
        )(distribution)
        if self._compiler_commands is not None:
            command.compiler_commands = self._compiler_commands
        command.build_lib = str(temporary / "lib")
        command.build_temp = str(temporary / "temp")
        command.ensure_finalized()
        try:
            if rust_text:
                archive, libraries = self._build_text(temporary / "rust")
                extension.extra_objects = [str(archive)]
                extension.extra_link_args = libraries
            command.run()
        except (
            BaseError,
            CCompilerError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as error:
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

    @staticmethod
    def _zlib_rs_archive(temporary: Path) -> Path | None:
        """Verify an explicitly supplied, privately prefixed static archive.

        The caller reviews/builds zlib-rs separately and supplies its expected
        SHA256. This hook never fetches code, resolves Cargo dependencies, or
        changes the platform inflater when the opt-in variable is absent.
        """
        selected = os.environ.get("UNSTD_GZIP_ZLIB_RS")
        if selected is None:
            return None
        if os.environ.get("CIBUILDWHEEL") == "1":
            message = (
                "UNSTD_GZIP_ZLIB_RS is a local qualification experiment; "
                "release wheels require a reviewed source/license/SBOM closure"
            )
            raise ValueError(message)
        expected = os.environ.get("UNSTD_GZIP_ZLIB_RS_SHA256", "")
        if not selected or re.fullmatch(r"[0-9a-fA-F]{64}", expected) is None:
            message = (
                "UNSTD_GZIP_ZLIB_RS requires a reviewed static archive path and "
                "UNSTD_GZIP_ZLIB_RS_SHA256 with its expected SHA256"
            )
            raise ValueError(message)
        archive = Path(selected).resolve(strict=True)
        if not archive.is_file() or archive.suffix not in {".a", ".lib"}:
            message = "UNSTD_GZIP_ZLIB_RS must name a static .a or .lib archive"
            raise ValueError(message)
        # The linker consumes only this private copy. Hashing the original and
        # then linking its mutable pathname would leave a replacement window.
        temporary.mkdir(parents=True, exist_ok=True)
        staged: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=temporary, suffix=archive.suffix, delete=False
            ) as output:
                staged = Path(output.name)
                with archive.open("rb") as stream:
                    shutil.copyfileobj(stream, output)
            with staged.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
        except BaseException:
            if staged is not None:
                staged.unlink(missing_ok=True)
            raise
        if actual != expected.lower():
            staged.unlink(missing_ok=True)
            message = "UNSTD_GZIP_ZLIB_RS archive does not match its expected SHA256"
            raise ValueError(message)
        return staged

    def _build_text(self, temporary: Path) -> tuple[Path, list[str]]:
        """Build the dependency-free Rust kernel and discover its system libraries."""
        manifest = Path(self.root) / "native/text/rust/Cargo.toml"
        archives = []
        libraries = []
        targets = self._rust_targets()
        for index, target in enumerate(targets):
            command = [
                os.environ.get("CARGO", "cargo"),
                "rustc",
                "--locked",
                "--offline",
                "--release",
                "--color",
                "never",
                "--manifest-path",
                str(manifest),
                "--target-dir",
                str(temporary / str(index)),
                "--message-format=json-render-diagnostics",
            ]
            if target is not None:
                command.extend(("--target", target))
            command.extend(("--", "--print=native-static-libs"))
            result = subprocess.run(  # noqa: S603  # nosec B603 # explicit compiler argv, no shell
                command, capture_output=True, text=True, check=False
            )
            messages = [result.stderr]
            outputs = []
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    messages.append(line)
                    continue
                if (
                    event.get("reason") == "compiler-artifact"
                    and event.get("target", {}).get("name") == "unstd_text"
                ):
                    outputs.extend(
                        Path(name)
                        for name in event.get("filenames", [])
                        if Path(name).suffix in {".a", ".lib"}
                    )
                elif event.get("reason") == "compiler-message":
                    messages.append(event.get("message", {}).get("rendered", ""))
            diagnostics = "\n".join(messages)
            if result.returncode:
                message = f"Rust text kernel build failed:\n{diagnostics}"
                raise RuntimeError(message)
            dependencies = re.findall(r"native-static-libs:\s*([^\r\n]*)", diagnostics)
            if len(outputs) != 1 or not outputs[0].is_file() or not dependencies:
                message = (
                    "Cargo did not report the text static library and its system "
                    f"dependencies:\n{diagnostics}"
                )
                raise RuntimeError(message)
            current = shlex.split(dependencies[-1])
            if archives and libraries != current:
                message = "Rust architecture system libraries differ"
                raise RuntimeError(message)
            archives.append(outputs[0])
            libraries = current
        if len(archives) == 1:
            return archives[0], libraries
        # Match setuptools' macOS universal2 C objects with a universal archive.
        output = temporary / "libunstd_text.a"
        lipo = shutil.which("lipo")
        if lipo is None:
            message = "macOS universal Rust archives require lipo"
            raise RuntimeError(message)
        subprocess.run(  # noqa: S603  # nosec B603 # fixed tool and explicit local archive paths
            [
                lipo,
                "-create",
                *(str(path) for path in archives),
                "-output",
                str(output),
            ],
            check=True,
        )
        return output, libraries

    @staticmethod
    def _rust_targets() -> list[str | None]:
        """Honor explicit Cargo targets and the C compiler's macOS architectures."""
        target = os.environ.get("CARGO_BUILD_TARGET")
        if target:
            return [target]
        if sys.platform == "darwin":
            flags = shlex.split(os.environ.get("ARCHFLAGS", ""))
            architectures = [
                flags[index + 1]
                for index, flag in enumerate(flags[:-1])
                if flag == "-arch"
            ]
            if not architectures:
                architectures = [sysconfig.get_platform().rsplit("-", 1)[-1]]
            if architectures == ["universal2"]:
                architectures = ["x86_64", "arm64"]
            return [
                f"{'aarch64' if arch == 'arm64' else arch}-apple-darwin"
                for arch in dict.fromkeys(architectures)
            ]
        if sys.platform == "win32":
            architecture = sysconfig.get_platform()
            return [
                {
                    "win32": "i686-pc-windows-msvc",
                    "win-amd64": "x86_64-pc-windows-msvc",
                    "win-arm64": "aarch64-pc-windows-msvc",
                }[architecture]
            ]
        # Linux wheel containers select their native GNU/musl toolchain. Cross
        # builds select CARGO_BUILD_TARGET alongside the corresponding C tools.
        return [None]

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
