"""Build the authenticated libsndfile fix and a host-qualified SoundFile wheel.

No installed packages are modified. External FLAC/Vorbis/Opus/LAME/mpg123 codecs
are required and retained; the resulting wheel requires their runtime libraries
on a compatible host. It does not claim manylinux or older-macOS portability.
"""

from __future__ import annotations

import argparse
import base64
import csv
from datetime import UTC, datetime
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
from typing import TYPE_CHECKING, Any
import zipfile

from tests.tooling.runtime import audio


if TYPE_CHECKING:
    from collections.abc import Sequence


def authenticated_bytes(path: Path, expected: str) -> bytes:
    """Reject an input before extraction or execution unless its pinned hash matches."""
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        msg = f"authenticated input SHA256 mismatch: {path}"
        raise ValueError(msg)
    return data


def backport_bytes(official: bytes) -> bytes:
    """Adapt only the released parser's read counter spelling, never patch fuzz."""
    if hashlib.sha256(official).hexdigest() != audio.PATCH_SHA256:
        msg = "the official fix patch has not been authenticated"
        raise ValueError(msg)
    adapted = official.replace(
        b"read_bytes = header_read", b"byte_count += header_read"
    )
    if hashlib.sha256(adapted).hexdigest() != audio.BACKPORT_SHA256:
        msg = "the reviewed 1.2.2 backport bytes do not match"
        raise ValueError(msg)
    return adapted


def _run(
    argv: Sequence[str],
    output: Path,
    commands: list[list[str]],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    commands.append(list(argv))
    # Build tools execute authenticated source with explicit argv, never a shell.
    result = subprocess.run(  # noqa: S603
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    with output.open("a", encoding="utf-8") as log:
        log.write(json.dumps(list(argv)) + "\n" + result.stdout + "\n")
    if result.returncode:
        msg = f"build command failed ({result.returncode}); see {output}: {argv[0]}"
        raise RuntimeError(msg)
    return result.stdout


def _cache(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith(("//", "#")) or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.split(":", 1)[0]] = value
    return values


def _stage_codec_archive(source: Path, destination: Path) -> dict[str, str]:
    """Snapshot codec bytes so the hashed file is the one the linker consumes."""
    destination.write_bytes(source.read_bytes())
    return {
        "source_path": str(source),
        "path": str(destination),
        "sha256": audio.file_sha256(destination),
    }


def _stage_binding(stage: Path, library: Path) -> dict[str, str]:
    _soundfile = importlib.import_module("_soundfile")
    soundfile = importlib.import_module("soundfile")

    binding: Any = soundfile
    if soundfile.__version__ != "0.14.0":
        msg = "this reviewed recipe requires SoundFile 0.14.0 as the Python binding"
        raise ValueError(msg)
    name = binding._packaged_libname
    if not isinstance(name, str) or Path(name).name != name:
        msg = "SoundFile has no reviewed platform library filename"
        raise ValueError(msg)
    stage.mkdir()
    for module in (soundfile, _soundfile):
        if module.__file__ is None:
            msg = "SoundFile binding modules must have concrete source files"
            raise ValueError(msg)
        origin = Path(module.__file__).resolve()
        shutil.copy2(origin, stage / origin.name)
    data = stage / "_soundfile_data"
    data.mkdir()
    (data / "__init__.py").write_text(
        '"""Authenticated patched libsndfile artifact."""\n'
    )
    shutil.copy2(library, data / name)
    return {
        str(path.relative_to(stage)): audio.file_sha256(path)
        for path in stage.rglob("*")
        if path.is_file()
    }


def _wheel(stage: Path, output: Path, source: Path) -> Path:
    version = "0.14.0+unstd.474e4d3"
    if sys.platform == "darwin":
        # CPython's configured target can predate this newly compiled artifact.
        # The builder explicitly targets this host's major release, never that
        # interpreter's older deployment floor.
        major = platform.mac_ver()[0].split(".", 1)[0]
        native_tag = f"macosx_{major}_0_{platform.machine()}"
    else:
        native_tag = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    tag = "py3-none-" + native_tag
    info = f"soundfile-{version}.dist-info"
    distribution = importlib.metadata.distribution("soundfile")
    metadata = distribution.read_text("METADATA")
    if metadata is None or "Version: 0.14.0\n" not in metadata:
        msg = "SoundFile 0.14.0 distribution metadata is required"
        raise ValueError(msg)
    files: dict[str, bytes] = {}
    for path in stage.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            name = str(path.relative_to(stage)).replace(os.sep, "/")
            files[f"{info}/{name}" if name.startswith("licenses/") else name] = (
                path.read_bytes()
            )
    files[f"{info}/METADATA"] = metadata.replace(
        "Version: 0.14.0\n", f"Version: {version}\n", 1
    ).encode()
    files[f"{info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: unstd-authenticated-audio-build\n"
        f"Root-Is-Purelib: false\nTag: {tag}\n"
    ).encode()
    files[f"{info}/licenses/libsndfile-COPYING"] = (source / "COPYING").read_bytes()
    for item in distribution.files or ():
        if ".dist-info" in str(item) and "license" in str(item).lower():
            relative = str(item).split(".dist-info/", 1)[1]
            files[f"{info}/{relative}"] = Path(
                str(distribution.locate_file(item))
            ).read_bytes()
    build_notes = (
        b"This host-qualified SoundFile build includes patched libsndfile 1.2.2.\n"
        b"The artifact's inputs retain the authenticated source archive and fix.\n"
        b"When codecs are linked statically, their original notices are retained\n"
        b"under licenses/; exact archive hashes and compiler commands are recorded\n"
        b"in the separate provenance.json. Retain the complete build/source\n"
        b"artifact when redistributing or relinking this library.\n"
    )
    files.setdefault(f"{info}/license_notes.md", build_notes)
    files[f"{info}/unstd_build_notes.md"] = build_notes
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for name, data in sorted(files.items()):
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            .rstrip(b"=")
            .decode()
        )
        writer.writerow([name, f"sha256={digest}", len(data)])
    writer.writerow([f"{info}/RECORD", "", ""])
    files[f"{info}/RECORD"] = record.getvalue().encode()
    wheel = output / f"soundfile-{version}-{tag}.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return wheel


def capability_report() -> dict[str, Any]:
    """Exercise actual external codec encoders/decoders in the staged process."""
    soundfile = importlib.import_module("soundfile")
    np = importlib.import_module("numpy")
    probes: dict[str, Any] = {}
    for format_name, subtype in [
        ("FLAC", "PCM_16"),
        ("OGG", "VORBIS"),
        ("OGG", "OPUS"),
        ("MP3", "MPEG_LAYER_III"),
    ]:
        sink = io.BytesIO()
        soundfile.write(
            sink,
            np.zeros(4096, dtype="float32"),
            48000,
            format=format_name,
            subtype=subtype,
        )
        encoded = sink.getvalue()
        decoded, rate = soundfile.read(io.BytesIO(encoded), dtype="float32")
        if not len(decoded) or rate != 48000 or not np.isfinite(decoded).all():
            msg = f"rebuilt codec roundtrip failed: {format_name}/{subtype}"
            raise ValueError(msg)
        probes[f"{format_name}/{subtype}"] = {
            "encoded_bytes": len(encoded),
            "decoded_frames": len(decoded),
            "samplerate": rate,
        }
    return dict(
        audio.loaded_library_report(),
        formats=soundfile.available_formats(),
        subtypes={f: soundfile.available_subtypes(f) for f in ("FLAC", "OGG", "MP3")},
        codec_roundtrips=probes,
    )


def build(
    source: Path,
    patch: Path,
    output: Path,
    cmake: Path,
    *,
    jobs: int = 2,
    prefixes: Sequence[Path] = (),
    static_codecs: bool = False,
) -> Path:
    """Build pinned inputs and preserve compiler, source, capability and binary evidence."""
    source_bytes = authenticated_bytes(source, audio.SOURCE_SHA256)
    official = authenticated_bytes(patch, audio.PATCH_SHA256)
    adapted = backport_bytes(official)
    if not cmake.is_absolute() or not cmake.is_file() or jobs < 1:
        msg = "--cmake must be an existing absolute executable and --jobs positive"
        raise ValueError(msg)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    inputs.mkdir()
    shutil.copy2(Path(__file__), inputs / "audio_build.py")
    shutil.copy2(Path(audio.__file__), inputs / "audio.py")
    (inputs / "libsndfile-1.2.2.tar.xz").write_bytes(source_bytes)
    (inputs / "official.patch").write_bytes(official)
    backport = inputs / "backport.patch"
    backport.write_bytes(adapted)
    with tarfile.open(inputs / "libsndfile-1.2.2.tar.xz") as archive:
        archive.extractall(output / "source", filter="data")
    tree = output / "source/libsndfile-1.2.2"
    common = tree / "src/common.c"
    if audio.file_sha256(common) != audio.COMMON_BEFORE_SHA256:
        msg = "released common.c does not match the reviewed baseline"
        raise ValueError(msg)
    log = output / "build.log"
    commands: list[list[str]] = []
    patch_tool = shutil.which("patch")
    pkg_config = shutil.which("pkg-config")
    if patch_tool is None or pkg_config is None:
        msg = "patch and pkg-config are required, together with all codec development libraries"
        raise ValueError(msg)
    codec_versions = _run(
        [
            pkg_config,
            "--modversion",
            "flac",
            "vorbis",
            "vorbisenc",
            "ogg",
            "opus",
            "libmpg123",
            "lame",
        ],
        log,
        commands,
    ).splitlines()
    _run(
        [patch_tool, "--batch", "--fuzz=0", "-p1", "-i", str(backport)],
        log,
        commands,
        cwd=tree,
    )
    if audio.file_sha256(common) != audio.COMMON_AFTER_SHA256:
        msg = "patched common.c does not match the reviewed upstream-fix backport"
        raise ValueError(msg)
    build_dir = output / "build"
    install = output / "install"
    configure = [
        str(cmake),
        "-S",
        str(tree),
        "-B",
        str(build_dir),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        f"-DPYTHON_EXECUTABLE={sys.executable}",
        "-DBUILD_SHARED_LIBS=ON",
        "-DBUILD_PROGRAMS=OFF",
        "-DBUILD_EXAMPLES=OFF",
        "-DBUILD_TESTING=OFF",
        "-DENABLE_EXTERNAL_LIBS=ON",
        "-DENABLE_MPEG=ON",
        f"-DCMAKE_INSTALL_PREFIX={install}",
    ]
    if prefixes:
        configure.append(
            "-DCMAKE_PREFIX_PATH=" + ";".join(str(p.resolve()) for p in prefixes)
        )
    if sys.platform == "darwin":
        configure.append(
            f"-DCMAKE_OSX_DEPLOYMENT_TARGET={platform.mac_ver()[0].split('.', 1)[0]}.0"
        )
    codec_archives: dict[str, dict[str, str]] = {}
    if static_codecs:
        staged_codecs = inputs / "codec-archives"
        staged_codecs.mkdir()
        specs = [
            ("flac", "FLAC_LIBRARY", "libFLAC.a"),
            ("vorbis", "Vorbis_Vorbis_LIBRARY", "libvorbis.a"),
            ("vorbisenc", "Vorbis_Enc_LIBRARY", "libvorbisenc.a"),
            ("ogg", "OGG_LIBRARY", "libogg.a"),
            ("opus", "OPUS_LIBRARY", "libopus.a"),
            ("libmpg123", "mpg123_LIBRARY", "libmpg123.a"),
            ("lame", "MP3LAME_LIBRARY", "libmp3lame.a"),
        ]
        for module, variable, filename in specs:
            libdir = _run(
                [pkg_config, "--variable=libdir", module], log, commands
            ).strip()
            archive_path = (Path(libdir) / filename).resolve(strict=True)
            # The linker consumes this private snapshot, not a package-manager
            # path that could change between hashing and the native link step.
            staged_archive = staged_codecs / f"{module}.a"
            codec_archives[module] = _stage_codec_archive(archive_path, staged_archive)
            configure.append(f"-D{variable}={staged_archive}")
    _run(configure, log, commands)
    _run(
        [
            str(cmake),
            "--build",
            str(build_dir),
            "--config",
            "Release",
            "--parallel",
            str(jobs),
        ],
        log,
        commands,
    )
    _run(
        [str(cmake), "--install", str(build_dir), "--config", "Release"], log, commands
    )
    candidates = {
        p.resolve()
        for p in install.rglob("*")
        if p.is_file()
        and (p.name.startswith(("libsndfile", "sndfile")))
        and (".dylib" in p.name or ".so" in p.name or p.suffix == ".dll")
    }
    if len(candidates) != 1:
        msg = f"cannot select a unique built shared library: {sorted(map(str, candidates))}"
        raise ValueError(msg)
    library = candidates.pop()
    linkage = ""
    if sys.platform == "darwin":
        otool = shutil.which("otool")
        if otool is None:
            msg = "otool is required to verify the built macOS load shape"
            raise ValueError(msg)
        linkage = _run([otool, "-L", str(library)], log, commands)
        if static_codecs and any(
            not line.strip().startswith(("/usr/lib/", "/System/Library/"))
            for line in linkage.splitlines()[2:]
        ):
            msg = "static-codec build acquired unexpected non-system dylib dependencies"
            raise ValueError(msg)
    modules = _stage_binding(output / "stage", library)
    if static_codecs:
        licenses = output / "stage" / "licenses"
        licenses.mkdir()
        for module in codec_archives:
            prefix = Path(
                _run([pkg_config, "--variable=prefix", module], log, commands).strip()
            )
            notices = [
                p
                for p in prefix.iterdir()
                if p.is_file()
                and any(
                    term in p.name.lower() for term in ("copying", "license", "authors")
                )
            ]
            if not notices:
                names = {
                    "flac": "libflac",
                    "vorbis": "libvorbis",
                    "vorbisenc": "libvorbis",
                    "ogg": "libogg",
                    "opus": "libopus",
                    "libmpg123": "libmpg123",
                    "lame": "libmp3lame",
                }
                notices = list(
                    (prefix / "share/doc").glob(f"{names[module]}*/copyright")
                )
            if not notices:
                msg = f"static codec {module} needs its original license notices in {prefix}"
                raise ValueError(msg)
            for notice in notices:
                shutil.copy2(notice, licenses / f"{module}-{notice.name}")
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(output / "stage") + os.pathsep + str(Path(__file__).resolve().parents[3])
    )
    inspection = _run(
        [
            sys.executable,
            "-c",
            "import json; from tests.tooling.runtime.audio_build import capability_report; print(json.dumps(capability_report()))",
        ],
        log,
        commands,
        env=env,
    )
    loaded = json.loads(inspection)
    if loaded["library_sha256"] != audio.file_sha256(library):
        msg = "the staged CFFI binding did not load the newly built library"
        raise ValueError(msg)
    for format_name, subtype in [
        ("FLAC", "PCM_16"),
        ("OGG", "VORBIS"),
        ("OGG", "OPUS"),
        ("MP3", "MPEG_LAYER_III"),
    ]:
        if subtype not in loaded["subtypes"][format_name]:
            msg = f"rebuilt library lost required codec capability {format_name}/{subtype}"
            raise ValueError(msg)
    wheel = _wheel(output / "stage", output, tree)
    cache = _cache(build_dir / "CMakeCache.txt")
    compiler = Path(cache["CMAKE_C_COMPILER"]).resolve()
    compiler_version = _run([str(compiler), "--version"], log, commands)
    cmake_version = _run([str(cmake), "--version"], log, commands)
    manifest = {
        "schema": 1,
        "recipe": audio.RECIPE,
        "created_utc": datetime.now(UTC).isoformat(),
        "inputs": {
            "source_url": audio.SOURCE_URL,
            "source_sha256": audio.SOURCE_SHA256,
            "fix_commit": audio.FIX_COMMIT,
            "official_patch_url": audio.PATCH_URL,
            "official_patch_sha256": audio.PATCH_SHA256,
            "backport_sha256": audio.BACKPORT_SHA256,
            "backport_adaptation": "13 header_read assignments retain 1.2.2 byte_count accumulation",
            "common_before_sha256": audio.COMMON_BEFORE_SHA256,
            "common_after_sha256": audio.COMMON_AFTER_SHA256,
        },
        "build": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "compiler": str(compiler),
            "compiler_sha256": audio.file_sha256(compiler),
            "compiler_version": compiler_version,
            "cmake": str(cmake),
            "cmake_sha256": audio.file_sha256(cmake),
            "cmake_native_executable": cache.get("CMAKE_COMMAND", str(cmake)),
            "cmake_native_sha256": audio.file_sha256(
                Path(cache.get("CMAKE_COMMAND", str(cmake)))
            ),
            "cmake_version": cmake_version,
            "commands": commands,
            "cmake_cache": cache,
            "builder_sha256": audio.file_sha256(Path(__file__)),
            "native_linkage": linkage,
            "codec_pkg_config_versions": dict(
                zip(
                    ["flac", "vorbis", "vorbisenc", "ogg", "opus", "libmpg123", "lame"],
                    codec_versions,
                    strict=True,
                )
            ),
            "static_codec_archives": codec_archives,
        },
        "output": {
            "library_path": loaded["library_path"],
            "library_sha256": loaded["library_sha256"],
            "native_version": loaded["native_version"],
            "binding_version": loaded["soundfile"],
            "staged_modules_sha256": modules,
            "formats": loaded["formats"],
            "subtypes": loaded["subtypes"],
            "codec_roundtrips": loaded["codec_roundtrips"],
            "wheel": wheel.name,
            "wheel_sha256": audio.file_sha256(wheel),
            "portability": "host-qualified; compatible external codec runtime libraries required",
        },
    }
    manifest_path = output / "provenance.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (output / "provenance.sha256").write_text(audio.file_sha256(manifest_path) + "\n")
    return manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    """Build into a new output directory without mutating the invoking environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cmake", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--prefix", type=Path, action="append", default=[])
    parser.add_argument(
        "--static-codecs",
        action="store_true",
        help="require and link all seven codec static archives",
    )
    parser.add_argument(
        "--trust-policy",
        type=Path,
        help="publish a new policy only for this successful trusted-input build",
    )
    args = parser.parse_args(argv)
    if args.trust_policy is not None and args.trust_policy.exists():
        parser.error("refusing to overwrite an existing build trust policy")
    try:
        manifest = build(
            args.source,
            args.patch,
            args.output,
            args.cmake,
            jobs=args.jobs,
            prefixes=args.prefix,
            static_codecs=args.static_codecs,
        )
        if args.trust_policy is not None:
            args.trust_policy.parent.mkdir(parents=True, exist_ok=True)
            with args.trust_policy.open("x", encoding="utf-8") as policy:
                json.dump(
                    {
                        "schema": 1,
                        "provenance_sha256": [audio.file_sha256(manifest)],
                        "recipe": audio.RECIPE,
                        "origin": "successful authenticated-input build in this trusted job",
                    },
                    policy,
                    indent=2,
                )
                policy.write("\n")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Audio build rejected: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "provenance": str(manifest),
                "provenance_sha256": audio.file_sha256(manifest),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
