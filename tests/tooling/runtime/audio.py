"""Admit SoundFile's actual loaded library against reviewed build provenance.

This external provisioning/release tool is never imported by unstd. A version
string, a SoundFile search-path variable, or a self-asserted provenance file does
not identify a patched binary: admission also requires the manifest's SHA256 in
a separately trusted review policy and the actual loaded library's byte hash.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import importlib
import json
from pathlib import Path
import platform
import sys
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from collections.abc import Sequence


POLICY_REVIEWED = "2026-10-22"
RECIPE = "libsndfile-1.2.2+474e4d3-backport1"
SOURCE_URL = "https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz"
SOURCE_SHA256 = "3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e"
FIX_COMMIT = "474e4d328b1e6240b93ec6ed14efb7c6a44bee57"
PATCH_URL = f"https://github.com/libsndfile/libsndfile/commit/{FIX_COMMIT}.patch"
PATCH_SHA256 = "9be5ac68e236d2ff2011f68065eacbf1b6daf4f9aa37a9f21ac9d74135697cba"
BACKPORT_SHA256 = "2a82701289a89f17c140cb27c973efa384b7133b7debfe8b60046afa81a3fba7"
COMMON_BEFORE_SHA256 = (
    "d1783a2c8472ff8ab30cd2c4033388ba01809954f8c33cedbaea5044b13eed79"
)
COMMON_AFTER_SHA256 = "a464aa40099f44e835f54b37c3ed868db5ac5b3773b6cfd9271f960bad4cf6be"
DEFAULT_TRUST_POLICY = (
    Path(__file__).resolve().parents[3] / "native/audio/reviewed-builds.json"
)


class _DlInfo(ctypes.Structure):
    """Platform dladdr result, populated by the loader."""


_DlInfo._fields_ = [
    ("dli_fname", ctypes.c_char_p),
    ("dli_fbase", ctypes.c_void_p),
    ("dli_sname", ctypes.c_char_p),
    ("dli_saddr", ctypes.c_void_p),
]


def file_sha256(path: Path) -> str:
    """Hash an artifact without loading all its bytes into memory."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def library_path_for_address(address: int) -> Path:
    """Resolve an actual native function address with the platform loader."""
    if not address:
        msg = "the loaded libsndfile function has no address"
        raise ValueError(msg)
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = ctypes.c_void_p()
        get_handle = kernel.GetModuleHandleExW
        get_handle.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        get_handle.restype = ctypes.c_int
        # FROM_ADDRESS | UNCHANGED_REFCOUNT; no library load/search is performed.
        if not get_handle(0x00000004 | 0x00000002, address, ctypes.byref(handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        get_name = kernel.GetModuleFileNameW
        get_name.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        get_name.restype = ctypes.c_uint
        name = ctypes.create_unicode_buffer(32768)
        count = get_name(handle, name, len(name))
        if not count or count >= len(name):
            msg = "cannot resolve the loaded libsndfile DLL path"
            raise OSError(msg)
        return Path(name.value).resolve(strict=True)
    loader = ctypes.CDLL(ctypes.util.find_library("dl") or None)
    resolve = loader.dladdr
    resolve.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DlInfo)]
    resolve.restype = ctypes.c_int
    info = _DlInfo()
    if not resolve(address, ctypes.byref(info)) or not info.dli_fname:
        msg = "dladdr could not identify SoundFile's actual loaded libsndfile"
        raise OSError(msg)
    return Path(info.dli_fname.decode(sys.getfilesystemencoding())).resolve(strict=True)


def loaded_library_report() -> dict[str, Any]:
    """Locate SoundFile's live CFFI symbol rather than trusting module metadata."""
    soundfile = importlib.import_module("soundfile")

    # SoundFile exposes this CFFI binding internally; release validation checks
    # the capability explicitly and fails closed if a future backend changes it.
    binding: Any = soundfile
    ffi = binding._ffi
    handle = binding._snd
    function = ffi.addressof(handle, "sf_version_string")
    address = int(ffi.cast("uintptr_t", function))
    library = library_path_for_address(address)
    version = ffi.string(handle.sf_version_string()).decode("ascii")
    return {
        "python": platform.python_version(),
        "executable": sys.executable,
        "soundfile": soundfile.__version__,
        "native_version": version,
        "symbol": "sf_version_string",
        "symbol_address": address,
        "library_path": str(library),
        "library_sha256": file_sha256(library),
        "library_bytes": library.stat().st_size,
    }


def provenance_errors(
    loaded: dict[str, Any], manifest_bytes: bytes, trusted_digests: Sequence[str]
) -> tuple[str, ...]:
    """Check reviewed source/fix evidence and the loaded binary without version bypasses."""
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    if digest not in trusted_digests:
        return ("provenance manifest is not in the separately trusted review policy",)
    try:
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict):
            return ("reviewed provenance must be a JSON object",)
        expected = {
            "source_sha256": SOURCE_SHA256,
            "official_patch_sha256": PATCH_SHA256,
            "backport_sha256": BACKPORT_SHA256,
            "common_before_sha256": COMMON_BEFORE_SHA256,
            "common_after_sha256": COMMON_AFTER_SHA256,
            "fix_commit": FIX_COMMIT,
        }
        errors: list[str] = []
        if (
            type(manifest.get("schema")) is not int
            or manifest.get("schema") != 1
            or manifest.get("recipe") != RECIPE
        ):
            errors.append("unreviewed provenance schema or build recipe")
        inputs = manifest.get("inputs", {})
        if not isinstance(inputs, dict):
            return ("reviewed provenance has no input evidence",)
        for key, value in expected.items():
            if inputs.get(key) != value:
                errors.append(
                    f"reviewed provenance does not contain the required {key}"
                )
        output = manifest.get("output", {})
        if not isinstance(output, dict):
            return ("reviewed provenance has no output evidence",)
        expected_hash = output.get("library_sha256")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(c not in "0123456789abcdef" for c in expected_hash)
            or expected_hash != loaded.get("library_sha256")
        ):
            errors.append(
                "actual loaded libsndfile hash is not the reviewed patched binary"
            )
        if output.get("native_version") != "libsndfile-1.2.2" or output.get(
            "native_version"
        ) != loaded.get("native_version"):
            errors.append("loaded native identity disagrees with reviewed provenance")
        return tuple(errors)
    except (ValueError, TypeError) as error:
        return (f"cannot parse reviewed provenance: {error}",)


def trusted_manifest_digests(path: Path) -> tuple[str, ...]:
    """Read digests from a review policy supplied through a trusted channel."""
    policy = json.loads(path.read_bytes())
    if (
        not isinstance(policy, dict)
        or type(policy.get("schema")) is not int
        or policy.get("schema") != 1
    ):
        msg = "unreviewed audio trust-policy schema"
        raise ValueError(msg)
    entries = policy.get("provenance_sha256", [])
    if not isinstance(entries, list) or any(
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
        for value in entries
    ):
        msg = "audio trust policy must contain SHA256 manifest digests"
        raise ValueError(msg)
    return tuple(entries)


def runtime_report(provenance: Path | None, trust_policy: Path) -> dict[str, Any]:
    """Inspect the loaded engine and fail closed on missing or unknown evidence."""
    report: dict[str, Any] = {
        "schema": 1,
        "policy_reviewed": POLICY_REVIEWED,
        "trust_policy": str(trust_policy),
    }
    try:
        report.update(loaded_library_report())
        if provenance is None:
            report["errors"] = ["no reviewed patched-build provenance was supplied"]
        else:
            data = provenance.read_bytes()
            report["provenance"] = str(provenance)
            report["provenance_sha256"] = hashlib.sha256(data).hexdigest()
            report["errors"] = list(
                provenance_errors(report, data, trusted_manifest_digests(trust_policy))
            )
    except (ImportError, OSError, AttributeError, ValueError, TypeError) as error:
        report["errors"] = [f"cannot admit the actual loaded audio library: {error}"]
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Emit offline admission evidence for an explicitly provisioned interpreter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", type=Path)
    parser.add_argument("--trust-policy", type=Path, default=DEFAULT_TRUST_POLICY)
    parser.add_argument("--json", action="store_true", help="emit structured evidence")
    args = parser.parse_args(argv)
    report = runtime_report(args.provenance, args.trust_policy)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Loaded audio library: {report.get('library_path', 'unresolved')}")
        for error in cast("list[str]", report["errors"]):
            print(f"Audio runtime rejected: {error}", file=sys.stderr)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
