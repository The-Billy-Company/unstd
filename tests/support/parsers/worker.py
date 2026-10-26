"""Bounded subprocess entry point for hostile parser and regex inputs."""

from __future__ import annotations

import base64
import importlib
import json
import os
from pathlib import Path
import re
import sys


MAX_INPUT = 2 * 1024 * 1024
_SOURCE = Path(__file__).resolve().parents[3] / "src"


class _Observed:
    """Delegate real backend operations while recording which engine executed."""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.calls = []

    def __getattr__(self, name):
        member = getattr(self.wrapped, name)
        if not callable(member):
            return member

        def observed(*args, **kwargs):
            self.calls.append(name)
            return member(*args, **kwargs)

        return observed


def _emit(result):
    import unstd

    result["origin"] = str(Path(unstd.__file__).resolve())
    print(json.dumps(result))


def _limits() -> None:
    """Enforce CPU and memory budgets, accounting for ASan's shadow mapping."""
    if sys.platform == "linux":
        import resource

        # Limits run inside the new interpreter; preexec_fn is unsafe with threads.
        resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
        if os.environ.get("UNSTD_NATIVE_SANITIZERS") == "1":
            assert "libasan" in os.environ.get("LD_PRELOAD", ""), (
                "ASan runtime required"
            )
            assert "hard_rss_limit_mb=512" in os.environ.get("ASAN_OPTIONS", "").split(
                ":"
            ), "ASan resident-memory budget required"
        else:
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))


def main() -> None:
    """Decode a test-owned envelope and execute exactly one public parser call."""
    envelope = json.loads(sys.stdin.buffer.read(MAX_INPUT + 1))
    if "package_root" in envelope:
        sys.path.insert(0, envelope["package_root"])
    elif envelope.get("source", True):
        sys.path.insert(0, str(_SOURCE))
    _limits()
    target = envelope["target"]
    raw = base64.b64decode(envelope["raw"], validate=True)
    if len(raw) > MAX_INPUT:
        msg = "worker input exceeds its explicit budget"
        raise ValueError(msg)
    if target not in {"json", "ndjson", "yaml", "regex"}:
        msg = f"unknown worker target {target!r}"
        raise ValueError(msg)
    try:
        if target == "json":
            from unstd.serde import jsonx

            value = jsonx.loads(raw)
        elif target == "ndjson":
            from unstd.serde import ndjson

            value = ndjson.loads(raw)
        elif target == "yaml":
            from unstd import yaml

            value = getattr(yaml, envelope.get("operation", "loads"))(raw)
        else:
            from unstd import rex

            pattern = rex.compile(envelope["pattern"])
            native = not isinstance(pattern, re.Pattern)
            observed_native = observed_python = None
            if envelope.get("attest") == "native":
                assert native, "the requested pattern did not compile natively"
                observed_native = _Observed(pattern._native)
                observed_python = _Observed(pattern._python)
                pattern._native = observed_native
                pattern._python = observed_python
            result = pattern.search(raw.decode("ascii"))
            _emit(
                {
                    "kind": "value",
                    "matched": result is not None,
                    "native": native,
                    "native_calls": observed_native.calls if observed_native else [],
                    "fallback_calls": observed_python.calls if observed_python else [],
                }
            )
            return
    except (ValueError, RecursionError) as exc:
        # The parent asserts exact permitted rejection classes for each case.
        _emit({"kind": "error", "type": type(exc).__name__})
        return
    except Exception as exc:
        # Only YAML's documented hierarchy is accepted here. Unexpected failures
        # escape with a nonzero exit, never a successful input rejection.
        if target != "yaml":
            raise
        error = importlib.import_module("unstd.yaml").YAMLError
        if not isinstance(exc, error):
            raise
        _emit({"kind": "error", "type": type(exc).__name__})
        return
    result = {"kind": "value", "type": type(value).__name__}
    if envelope.get("inspect") == "value":
        assert len(raw) <= 4096  # Only small reviewed success controls are serialized.
        result["value"] = value
    elif envelope.get("inspect") == "alias-graph":
        depth = int(envelope["depth"])
        assert 1 <= depth <= 20
        result["summary"] = {
            "keys": sorted(value),
            "leaf": value["level0"],
            "identity": all(
                len(value[f"level{level}"]) == 20
                and all(
                    child is value[f"level{level - 1}"]
                    for child in value[f"level{level}"]
                )
                for level in range(1, depth + 1)
            ),
        }
    _emit(result)


if __name__ == "__main__":
    main()
