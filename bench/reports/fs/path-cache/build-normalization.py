"""Build the private-style normalization opportunity proof for local reruns."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import sysconfig


def main() -> None:
    if sys.platform not in {"darwin", "linux"}:
        message = "This diagnostic builder supports macOS and Linux only"
        raise RuntimeError(message)
    suffix: object = sysconfig.get_config_var("EXT_SUFFIX")
    if not isinstance(suffix, str):
        message = "The interpreter did not provide an extension suffix"
        raise TypeError(message)
    root = Path(__file__).resolve().parent
    source = root / "native-raw-clone.c"
    output = root / ("_path_probe" + suffix)
    command = ["cc", "-O3", "-shared", "-fPIC"]
    if sys.platform == "darwin":
        command.extend(["-undefined", "dynamic_lookup"])
    command.extend(["-I" + sysconfig.get_path("include"), str(source), "-o", str(output)])
    # Compiler flags are fixed; both paths name this local proof's owned files.
    subprocess.run(command, check=True)  # noqa: S603
    report = {
        "qualification": "Unqualified opportunity reference; no production API",
        "python": sys.version,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    (root / "normalization-build.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
