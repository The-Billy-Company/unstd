"""Build only the optional proc prototype for the selected interpreter."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext


ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    """Compile the private module independently from other native domains."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "src")
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("--warnings-as-errors", action="store_true")
    args = parser.parse_args()
    compile_args = []
    link_args = []
    if os.name != "nt":
        compile_args = ["-O3", "-Wall", "-Wextra"]
        if args.warnings_as_errors:
            compile_args.append("-Werror")
        if args.sanitize:
            compile_args = [
                "-O1",
                "-g",
                "-Wall",
                "-Wextra",
                "-fno-omit-frame-pointer",
                "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all",
                *(["-Werror"] if args.warnings_as_errors else []),
            ]
            link_args = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    elif args.sanitize:
        parser.error("the focused sanitizer build requires a POSIX compiler")
    module = Extension(
        "unstd.proc._proc_native",
        [str(ROOT / "native/proc/src" / name) for name in ("adapter.c", "pipes.c")],
        libraries=["dl"] if sys.platform == "linux" else [],
        extra_compile_args=compile_args,
        extra_link_args=link_args,
    )
    with tempfile.TemporaryDirectory(prefix="unstd-proc-build-") as temporary:
        command = build_ext(Distribution({"ext_modules": [module]}))
        command.build_lib = str(Path(temporary) / "lib")
        command.build_temp = temporary
        command.ensure_finalized()
        command.run()
        built = Path(command.get_ext_fullpath(module.name))
        target = args.output.resolve() / "unstd/proc" / built.name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Loaded Mach-O mappings retain their original inode and code signature.
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as staged:
            staged.write(built.read_bytes())
        staged_path = Path(staged.name)
        try:
            staged_path.chmod(built.stat().st_mode & 0o777)
            staged_path.replace(target)
        finally:
            staged_path.unlink(missing_ok=True)
        print(target)


if __name__ == "__main__":
    main()
