"""Isolated, explicitly admitted parser workers and their bounded wire protocol.

Run as a file under ``python -I``. No target, optional extension, pytest plugin,
or repository module loads before backend policy and resource limits apply.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import errno
import importlib
from importlib import metadata
import importlib.abc
import json
import os
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[3]
REQUIRED = {
    "json": ("orjson",),
    "base64": ("pybase64",),
    "ndjson": ("orjson",),
    "structs": ("msgspec",),
    "tokens": ("blake3",),
    "pack": ("numpy",),
    "wav": ("numpy", "soundfile"),
    "toml": ("tomlkit",),
    "yaml": ("ruamel.yaml",),
    "rex": ("irgx",),
    "gzip": ("zlib_ng",),
}
BLOCKED = (
    "orjson",
    "msgspec",
    "pybase64",
    "blake3",
    "whenever",
    "numpy",
    "soundfile",
    "irgx",
    "uuid_utils",
    "rapidfuzz",
    "tomlkit",
    "ruamel",
    "sortedcontainers",
    "immutables",
    "pydantic",
    "pydantic_settings",
    "google",
    "zlib_ng",
)
FIXTURES = frozenset(
    {
        "pass",
        "assertion",
        "unexpected",
        "memory",
        "address_space",
        "hang",
        "abort",
        "flood",
        "blocked",
        "startup",
        "descendant",
        "cpu",
    }
)
_WIRE = sys.stdout


class _BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in BLOCKED):
            msg = f"stdlib fuzz backend blocks {fullname}"
            raise ModuleNotFoundError(msg)
        return


def emit(event: str, **fields) -> None:
    """Send short machine-owned events independently of Python target output."""
    _WIRE.write(json.dumps({"event": event, **fields}, ensure_ascii=True) + "\n")
    _WIRE.flush()


def configure(backend: str, cpu_seconds: int, memory_mb: int) -> dict:
    """Apply controls before imports, reporting only controls actually enforced."""
    if backend not in {"installed", "stdlib"}:
        msg = f"unsupported backend: {backend}"
        raise ValueError(msg)
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    if backend == "stdlib":
        sys.meta_path.insert(0, _BlockOptional())
    result = {"address_space": False, "cpu": False, "core_dumps_disabled": False}
    if os.name == "posix":
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        result["core_dumps_disabled"] = True
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        result["cpu"] = True
        # Darwin's RLIMIT_AS is not a reliable process resident-memory bound.
        if sys.platform.startswith("linux"):
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            result["address_space"] = True
    return result


def load_target(name: str, backend: str, fixture: str | None = None):
    """Import a fixed reviewed entry point; absent installed extras are failures."""
    if fixture is not None:
        if fixture not in FIXTURES:
            msg = "unreviewed worker fixture"
            raise ValueError(msg)
        module = importlib.import_module(
            "tests.suites.assurance.gates.fuzz._execution_fixtures"
        )
        return module.target(fixture), {"fixture": fixture}
    if name not in REQUIRED:
        msg = f"unknown fuzz target: {name}"
        raise ValueError(msg)
    versions = {}
    if backend == "installed":
        distributions = metadata.packages_distributions()
        for required in REQUIRED[name]:
            module = importlib.import_module(required)
            packages = distributions.get(required.split(".")[0], ())
            versions[required] = {
                "module": getattr(module, "__file__", None),
                "distributions": {
                    package: metadata.version(package) for package in packages
                },
            }
    module = importlib.import_module(f"tests.tooling.fuzz.targets.{name}")
    return module.fuzz, versions


def arguments() -> argparse.Namespace:
    """Parse only fixed target identities and numeric execution limits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=sorted(REQUIRED))
    parser.add_argument("--backend", choices=("installed", "stdlib"), required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--max-input-bytes", type=int, required=True)
    parser.add_argument("--fixture", choices=sorted(FIXTURES))
    return parser.parse_args()


def main() -> int:
    """A startup event and one start/done pair per admitted input prove replay."""
    args = arguments()
    try:
        enforcement = configure(args.backend, args.cpu_seconds, args.memory_mb)
        with contextlib.redirect_stdout(sys.stderr):
            fuzz, versions = load_target(args.target, args.backend, args.fixture)
        emit("ready", enforcement=enforcement, backend_modules=versions)
    except BaseException as error:
        emit(
            "failure",
            kind="startup_error",
            exception=type(error).__name__,
            message=str(error)[:2048],
            traceback=traceback.format_exc(limit=12)[-8192:],
        )
        return 2
    for line in sys.stdin.buffer:
        try:
            if len(line) > args.max_input_bytes * 2 + 4096:
                msg = "oversized worker frame"
                raise ValueError(msg)  # noqa: TRY301 - report wire admission failures
            request = json.loads(line)
            if request == {"stop": True}:
                emit("complete")
                return 0
            case_id = request["id"]
            data = base64.b64decode(request["data"], validate=True)
            if len(data) > args.max_input_bytes:
                msg = "input exceeds worker admission limit"
                raise ValueError(msg)  # noqa: TRY301 - report wire admission failures
            emit("start", id=case_id)
            with contextlib.redirect_stdout(sys.stderr):
                fuzz(data)
            emit("done", id=case_id)
        except BaseException as error:
            kind = (
                "assertion"
                if isinstance(error, AssertionError)
                else "memory_error"
                if isinstance(error, MemoryError)
                or (isinstance(error, OSError) and error.errno == errno.ENOMEM)
                else "unexpected_exception"
            )
            emit(
                "failure",
                kind=kind,
                exception=type(error).__name__,
                message=str(error)[:2048],
                traceback=traceback.format_exc(limit=12)[-8192:],
            )
            return 1
    # Parent death or truncated traffic is not successful completion.
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
