"""Verify that an installed serde wheel retains its native JSON paths.

Run this file with the deployment interpreter, outside the source checkout if
possible. Correct output alone is insufficient: portable fallbacks can produce
the same output while losing the acceleration this preflight requires.
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import import_module
from importlib.machinery import EXTENSION_SUFFIXES
import json
from pathlib import Path
import platform
import sys
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import ModuleType


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _equal(actual: object, expected: object) -> None:
    _require(
        type(actual) is type(expected) and actual == expected,
        "native path declined the eligible fixture or changed its output",
    )


def _probes(
    native: ModuleType,
    jsonx: ModuleType,
    ndjson: ModuleType,
    encoding: ModuleType,
    orjson: ModuleType,
) -> dict[str, Callable[[], None]]:
    integers = [
        {"id": index, "text": "café😀\x7f,:", "ok": True} for index in range(32)
    ]
    floats = [
        {"id": index, "text": "café😀", "score": 1.5, "zero": -0.0, "small": 1e-5}
        for index in range(32)
    ]
    compact = json.dumps(integers, ensure_ascii=False, separators=(",", ":")).encode()
    float_bytes = json.dumps(floats, ensure_ascii=False, separators=(",", ":")).encode()

    def bindings() -> None:
        _require(
            jsonx._native is native and encoding._native is native,
            "public JSON encoder lost its native module",
        )
        for owner, name in (
            (jsonx, "_native_dumpb"),
            (jsonx, "_native_line"),
            (jsonx, "_native_identity_snapshot"),
            (encoding, "_native_encode"),
            (encoding, "_native_encode_text"),
            (encoding, "_native_ascii_text"),
            (ndjson, "_loads_lines"),
        ):
            _require(
                getattr(getattr(owner, name, None), "__self__", None) is native,
                f"{owner.__name__}.{name} lost its native binding",
            )
        _require(jsonx._encode is orjson.dumps, "public encoder lost orjson")
        _require(jsonx._HAVE_MSGSPEC is True, "public decoder lost msgspec")
        jsonx.loads("null")
        jsonx.dumpb(None)
        if sys.version_info[:2] == (3, 14):
            for function in (jsonx.loads, jsonx.dumpb):
                _require(
                    native.is_bound(function) and native.dispatch_ready(function),
                    f"{function.__name__} native function dispatch is inactive",
                )

    def encoding_probe() -> None:
        _equal(native.encode(integers), compact)
        _equal(native.encode_wide(integers, 0), compact)
        _equal(native.dumpb(integers, False, None, False), compact)
        _equal(jsonx.dumpb(integers), compact)
        _equal(
            native.encode_indent(integers, orjson.OPT_INDENT_2),
            json.dumps(integers, ensure_ascii=False, indent=2).encode(),
        )

    def float_probe() -> None:
        for ensure_ascii in (False, True):
            for spaced in (False, True):
                separators = (", ", ": ") if spaced else (",", ":")
                expected = json.dumps(
                    floats, ensure_ascii=ensure_ascii, separators=separators
                )
                _equal(native.encode_text(floats, ensure_ascii, spaced), expected)
                _equal(
                    jsonx.dumps(
                        floats, ensure_ascii=ensure_ascii, separators=separators
                    ),
                    expected,
                )
        _equal(native.dumpb(floats, False, None, False), float_bytes)
        _equal(jsonx.dumpb(floats), float_bytes)

    def ascii_probe() -> None:
        _equal(
            native.ascii_text(compact),
            json.dumps(integers, ensure_ascii=True, separators=(",", ":")),
        )
        _equal(jsonx.dumps(integers), json.dumps(integers))

    def decoding_probe() -> None:
        # Large integers, difficult floats and signed zero must survive intact.
        document = b'{"integer":1267650600228229401496703205376,"float":1.2345678901234567,"zero":-0.0}'
        expected = json.dumps(json.loads(document))
        _equal(json.dumps(jsonx.loads(document)), expected)
        _equal(json.dumps(jsonx.loads(document.decode())), expected)
        decoded = native.loads_lines(document + b"\n")
        _require(
            decoded is not NotImplemented,
            "native decoder declined an eligible exact-number document",
        )
        _equal(json.dumps(decoded), "[" + expected + "]")

    def identity_probe() -> None:
        jsonx._identity_snapshot(None, set())  # Refresh ordinary walker monitoring.
        branch = {"values": ["café😀", -0.0, 2**100]}
        source = [branch, branch]
        snapshot = native.identity_snapshot(source)
        _equal(snapshot, source)
        _require(
            snapshot is not source
            and snapshot[0] is not branch
            and snapshot[0] is not snapshot[1]
            and snapshot[0]["values"] is not branch["values"],
            "native strict identity snapshot lost private branch ownership",
        )
        _equal(
            jsonx.canonical_strict(source),
            json.dumps(source, sort_keys=True, separators=(",", ":")).encode(),
        )

    def ndjson_probe() -> None:
        expected = b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
            for row in integers
        )
        _equal(native.line(integers[0]), expected.splitlines(keepends=True)[0])
        _equal(ndjson.dumpb(integers), expected)
        _equal(native.loads_lines(expected), integers)
        _equal(ndjson.loads(expected), integers)

    return {
        "bindings": bindings,
        "encoding": encoding_probe,
        "repeated_floats": float_probe,
        "ascii": ascii_probe,
        "decoding": decoding_probe,
        "strict_identity": identity_probe,
        "ndjson": ndjson_probe,
    }


def runtime_report() -> dict[str, Any]:
    """Check actual native admission and retain interpreter/binary evidence."""
    errors: list[str] = []
    passed: list[str] = []
    report: dict[str, Any] = {
        "schema": 1,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "checks": passed,
        "errors": errors,
    }
    try:
        orjson = import_module("orjson")
        msgspec = import_module("msgspec")
        native = import_module("unstd.serde._json_native")
        jsonx = import_module("unstd.serde.jsonx")
        ndjson = import_module("unstd.serde.ndjson")
        # Follow the installed facade across package layout revisions.
        encoding = import_module(jsonx._exact_encode.__module__)
        binary = Path(native.__file__).resolve()
        _require(
            any(binary.name.endswith(suffix) for suffix in EXTENSION_SUFFIXES),
            "JSON native module is not a compiled extension",
        )
        report.update(
            {
                "native": str(binary),
                "native_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "jsonx": jsonx.__file__,
                "backends": {
                    "orjson": orjson.__version__,
                    "msgspec": msgspec.__version__,
                },
            }
        )
    except (ImportError, AttributeError, OSError, TypeError, RuntimeError) as error:
        errors.append(f"native installation: {type(error).__name__}: {error}")
        return report
    for name, probe in _probes(native, jsonx, ndjson, encoding, orjson).items():
        try:
            probe()
        except (AttributeError, TypeError, ValueError, RuntimeError) as error:
            errors.append(f"{name}: {type(error).__name__}: {error}")
        else:
            passed.append(name)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Fail deployment qualification when native JSON acceleration is missing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit structured evidence")
    args = parser.parse_args(argv)
    report = runtime_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"Python {report['python']}: {len(report['checks'])} native JSON checks passed"
        )
        for error in cast("list[str]", report["errors"]):
            print(f"JSON acceleration rejected: {error}", file=sys.stderr)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
