"""Build only the Linux fs experiment into a requested scratch directory."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig


def main() -> None:
    """Compile the local proof without modifying installed package files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--observer", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("the fs proof is Linux-only")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    output = args.output_dir / f"_fs_experiment{suffix}"
    command = [
        *shlex.split(sysconfig.get_config_var("CC") or "cc"),
        "-O3",
        "-std=c11",
        "-fPIC",
        "-shared",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-missing-field-initializers",
        f"-I{sysconfig.get_paths()['include']}",
        str(Path(__file__).with_name("commit.c")),
        "-o",
        str(output),
    ]
    subprocess.run(command, check=True)  # noqa: S603 — local source and configured compiler
    print(output)
    if args.observer:
        observer = args.output_dir / "observe.so"
        subprocess.run(  # noqa: S603 — fixed test observer and configured compiler
            [
                *shlex.split(sysconfig.get_config_var("CC") or "cc"),
                "-O2",
                "-std=c11",
                "-fPIC",
                "-shared",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(Path(__file__).with_name("observe.c")),
                "-ldl",
                "-o",
                str(observer),
            ],
            check=True,
        )
        print(observer)


if __name__ == "__main__":
    main()
