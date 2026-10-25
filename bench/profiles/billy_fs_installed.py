"""Checked Billy workloads through an installed Linux public fs backend.

Both lanes call ``fs.atomic_write`` from the same selected installation. One
module retains its packaged native backend; the other sets ``_native=None``.
Independent untimed routing checks prove native acceptance and forced fallback
before and after paired timing. Direct execution needs no optional packages.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.machinery import ExtensionFileLoader
import inspect
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from types import ModuleType
from typing import TYPE_CHECKING, Any
import zipfile


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import CodeType, FrameType


def _support() -> ModuleType:
    name = "_unstd_billy_fs_installed_support"
    path = Path(__file__).with_name("billy_fs.py")
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)  # noqa: S102 — trusted sibling driver
    return module


_BILLY = _support()
_BASE = _BILLY._BASE


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_path(module: ModuleType) -> Path:
    filename = getattr(module, "__file__", None)
    if filename is None:
        message = f"benchmark support module has no file provenance: {module.__name__}"
        raise RuntimeError(message)
    return Path(filename).resolve()


def _native_evidence(module: ModuleType, source: Path) -> dict[str, Any]:
    """Require the selected installation's real compiled public backend."""
    native = getattr(module, "_native", None)
    if native is None:
        message = "selected installation has no active Linux fs backend"
        raise RuntimeError(message)
    filename = getattr(native, "__file__", None)
    if filename is None:
        message = "active fs backend has no compiled-file provenance"
        raise RuntimeError(message)
    path = Path(filename).resolve()
    if not path.is_relative_to(source.resolve() / "unstd" / "fs"):
        message = f"fs backend escaped the selected installation: {path}"
        raise RuntimeError(message)
    spec = getattr(native, "__spec__", None)
    method = getattr(native, "atomic_write", None)
    if (
        spec is None
        or not isinstance(spec.loader, ExtensionFileLoader)
        or not inspect.isbuiltin(method)
        or method.__module__ != "unstd.fs._fs_native"
    ):
        message = "active fs atomic_write is not the installed compiled entrypoint"
        raise RuntimeError(message)
    return {
        "path": str(path),
        "relative_path": str(path.relative_to(source.resolve())),
        "sha256": _sha(path),
        "method": repr(method),
        "public_signature": str(inspect.signature(module.atomic_write)),
    }


def _load_pair(source: Path) -> tuple[ModuleType, ModuleType, dict[str, Any]]:
    """Isolate the two public modules while preserving identical package bytes."""
    native, native_source = _BASE._load_source(source)
    binary = _native_evidence(native, source)
    python, python_source = _BASE._load_source(source)
    if native is python:
        message = "native and fallback lanes share mutable module state"
        raise RuntimeError(message)
    sources = {"native": native_source, "python": python_source}
    _BASE._check_signatures(sources)
    if native_source["files_sha256"] != python_source["files_sha256"]:
        message = "installed package changed between native and fallback imports"
        raise RuntimeError(message)
    python._native = None
    return python, native, {"sources": sources, "native": binary}


def _wheel_evidence(wheel: Path, source_evidence: dict[str, Any]) -> dict[str, Any]:
    """Tie the selected installed fs sources and binary to an actual wheel."""
    expected = source_evidence["files_sha256"]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            message = "wheel contains duplicate members"
            raise RuntimeError(message)
        for relative, digest in expected.items():
            try:
                actual = hashlib.sha256(archive.read(relative)).hexdigest()
            except KeyError as error:
                message = f"wheel lacks installed fs file: {relative}"
                raise RuntimeError(message) from error
            if actual != digest:
                message = f"wheel differs from installed fs file: {relative}"
                raise RuntimeError(message)
    return {"path": str(wheel.resolve()), "sha256": _sha(wheel)}


def _route_proof(
    prepared: Any,
    native_function: Callable[..., Any],
    fallback_codes: Sequence[CodeType],
) -> dict[str, dict[str, int]]:
    """Reject dark/declined native work and borrowed results outside timing."""
    proof = {}
    for label in ("python", "native"):
        counts = {"native_calls": 0, "fallback_calls": 0}

        def profile(
            frame: FrameType,
            event: str,
            arg: Any,
            counts: dict[str, int] = counts,
        ) -> None:
            if event == "c_call" and arg is native_function:
                counts["native_calls"] += 1
            if event == "call" and any(frame.f_code is code for code in fallback_codes):
                counts["fallback_calls"] += 1

        prepared.setup(label)
        previous = sys.getprofile()
        try:
            sys.setprofile(profile)
            result = prepared.calls[label]()
        finally:
            sys.setprofile(previous)
        prepared.check(label, result)
        if label == "native":
            valid = counts["native_calls"] > 0 and counts["fallback_calls"] == 0
        else:
            valid = counts["native_calls"] == 0 and counts["fallback_calls"] > 0
        if not valid:
            message = f"public fs route was not accepted for {label}: {counts}"
            raise RuntimeError(message)
        proof[label] = counts
    return proof


def main(argv: list[str] | None = None) -> None:
    """Verify installed public routing, then retain checked paired evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--billy-source", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--rounds", type=_BASE._positive, default=9)
    parser.add_argument("--repetitions", type=_BASE._positive, default=3)
    parser.add_argument("--include", action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("installed native fs profiling requires Linux")
    fixture = _BILLY._load_fixtures(args.fixtures, args.billy_source)
    python, native, evidence = _load_pair(args.source)
    wheel = (
        _wheel_evidence(args.wheel, evidence["sources"]["native"])
        if args.wheel is not None
        else None
    )
    paths = [
        Path(__file__).resolve(),
        _module_path(_BILLY),
        _module_path(_BASE),
        args.fixtures.resolve(),
    ]
    if args.wheel is not None:
        paths.append(args.wheel.resolve())
    hashes = {str(path): _sha(path) for path in paths}
    report: dict[str, Any] = {
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "observer_loaded": bool(os.environ.get("LD_PRELOAD")),
        },
        "installation": evidence,
        "wheel": wheel,
        "fixtures": fixture,
        "harness_files_sha256": hashes,
        "method": "both lanes call installed public fs.atomic_write; forced _native=None fallback; paired warm-cache batches with real fsync; sentinel resets and routing/output checks outside timing",
        "workloads": [],
    }
    fallback_codes = (
        python._atomic_write_python.__code__,
        native._atomic_write_python.__code__,
    )
    with tempfile.TemporaryDirectory(
        prefix="unstd-billy-fs-installed-", dir=args.root
    ) as temporary:
        root = Path(temporary)
        report["environment"].update(
            fixture_root=str(root), fixture_device=root.stat().st_dev
        )
        prepared_cases = _BILLY._prepare(
            fixture, args.billy_source, root, python, native, public_native=True
        )
        if args.include:
            unknown = set(args.include) - {
                case.recipe["name"] for case in prepared_cases
            }
            if unknown:
                parser.error(f"unknown workloads: {sorted(unknown)}")
            prepared_cases = [
                case for case in prepared_cases if case.recipe["name"] in args.include
            ]
        for prepared in prepared_cases:
            proof_before = _route_proof(
                prepared, native._native.atomic_write, fallback_codes
            )
            result = _BILLY._measure(
                prepared, rounds=args.rounds, repetitions=args.repetitions
            )
            proof_after = _route_proof(
                prepared, native._native.atomic_write, fallback_codes
            )
            result["public_dispatch_proof"] = {
                "before": proof_before,
                "after": proof_after,
            }
            report["workloads"].append(result)
            print(result["name"], result["python_over_native"], flush=True)
    _BASE._check_source_hashes(evidence["sources"])
    _BILLY._load_fixtures(args.fixtures, args.billy_source)
    for filename, expected in hashes.items():
        if _sha(Path(filename)) != expected:
            message = (
                f"installed fs benchmark input changed during measurement: {filename}"
            )
            raise RuntimeError(message)
    report["sources_verified_after_timing"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
