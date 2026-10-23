"""Build the reviewed libdeflate 1.26 experiment in an isolated output tree."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "native/compress/experiments/libdeflate-1.26.json"
MANIFEST_SHA256 = "fd7091752852f228ac2fff657d2c851d10a4ceadbe106e8c5ea06c9fb4a50cc6"


def stage_source(source: Path, destination: Path) -> dict[str, Any]:
    """Verify the fixed review, then copy and authenticate its exact closure."""
    raw = MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        message = "libdeflate experiment review manifest changed"
        raise ValueError(message)
    manifest = json.loads(raw)
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        for name, expected in manifest["files"].items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected:
                message = f"libdeflate experiment source hash mismatch: {name}"
                raise ValueError(message)  # noqa: TRY301 -- remove the rejected stage
        (destination / "UPSTREAM.json").write_bytes(raw)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return manifest


def main() -> None:
    """Compile only the native experiment; never produce a release wheel."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT / "native"):
        parser.error("output must be outside the repository's native sources")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    source_root = output / "source"
    native = source_root / "native/compress"
    manifest = stage_source(args.source.resolve(), native / "libdeflate")
    for name in ("adapter.c", "stream.c", "stream.h", "inflate.c"):
        shutil.copyfile(ROOT / "native/compress" / name, native / name)
    shutil.copytree(ROOT / "native/compress/zlib", native / "zlib")
    shutil.copyfile(ROOT / "hatch_build.py", source_root / "hatch_build.py")
    spec = importlib.util.spec_from_file_location(
        "unstd_libdeflate_experiment", source_root / "hatch_build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    extension = module.Extension

    def configured(*values: Any, **options: Any) -> Any:
        options["extra_compile_args"] = [
            "/std:c11" if os.name == "nt" else "-std=gnu11"
        ]
        options["optional"] = False
        return extension(*values, **options)

    module.Extension = configured
    for key in (
        "UNSTD_GZIP_ZLIB_RS",
        "UNSTD_GZIP_ZLIB_RS_SHA256",
        "UNSTD_GZIP_ZLIB_RS_LINK_ARGS",
    ):
        os.environ.pop(key, None)
    hook = module.CustomBuildHook(
        str(source_root), {}, None, None, str(output), "wheel"
    )
    data = {"force_include": {}}
    hook._build_module(
        "unstd.compress._gzip_native", output / "build", "standard", data
    )
    (binary,) = data["force_include"]
    result = {
        "qualification_only": True,
        "binary": binary,
        "binary_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        "binary_bytes": Path(binary).stat().st_size,
        "review_manifest_sha256": MANIFEST_SHA256,
        "upstream": manifest,
        "build": data,
    }
    (output / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
