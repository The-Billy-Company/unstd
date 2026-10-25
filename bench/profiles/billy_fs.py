"""Linux filesystem proof for captured atomic-write consumers.

Fixtures contain synthetic payload recipes and source receipts, never credentials.
Run this file directly. The hosted-state and notebook reference functions load
from checked source ASTs without importing their application dependencies.
Candidate hosted functions replace only the captured atomic-publication block.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from functools import partial
import hashlib
from importlib.machinery import ExtensionFileLoader
import importlib.util
import inspect
import json
import os
from pathlib import Path
import platform
import stat
import sys
import tempfile
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast


if TYPE_CHECKING:
    from collections.abc import Callable


_ROOT = Path(__file__).resolve().parents[2]
_CONSUMERS = {
    "oauth": ("services/ai/scripts/auth/google_oauth.py", "main"),
    "config": ("services/ai/scripts/lint/refresh_import_baseline.py", "main"),
    "report": ("tools/igor/connectors/storage/capture/probe.py", "write_snapshot"),
    "hosted": (
        "services/ai/tools/system/control/browser/hosted/custody.py",
        "land_state",
    ),
    "notebook": ("libs/runtime/jupyter/billy_jupyter/policy.py", "atomic"),
    "retention": ("libs/runtime/jupyter/billy_jupyter/retention.py", "checkpoint"),
}
_INPUTS = {
    "config": "services/ai/.importlinter",
    "report": "tools/igor/connectors/storage/capture/fixture.json",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _support() -> ModuleType:
    name = "_unstd_fs_profile_support"
    path = Path(__file__).with_name("fs_backend.py")
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)  # noqa: S102 — trusted sibling harness
    return module


_BASE = _support()


def _symbol(path: Path, symbol: str) -> tuple[str, ast.FunctionDef]:
    source = path.read_text(encoding="utf-8")
    nodes = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == symbol
    ]
    if len(nodes) != 1:
        message = f"ambiguous or missing consumer: {path}:{symbol}"
        raise ValueError(message)
    return source, nodes[0]


def _contracts(root: Path) -> dict[str, Any]:
    receipts = {}
    for name, (relative, symbol) in _CONSUMERS.items():
        path = root / relative
        source, node = _symbol(path, symbol)
        segment = ast.get_source_segment(source, node)
        assert segment is not None
        receipts[name] = {
            "path": relative,
            "symbol": symbol,
            "line": node.lineno,
            "end_line": node.end_lineno,
            "file_sha256": _sha(path.read_bytes()),
            "symbol_sha256": _sha(segment.encode()),
        }
    for name, relative in _INPUTS.items():
        raw = (root / relative).read_bytes()
        receipts[f"{name}_input"] = {
            "path": relative,
            "file_sha256": _sha(raw),
            "bytes": len(raw),
        }
    return receipts


def _synthetic(value: Any) -> Any:
    """Keep the bundled report schema while replacing every string leaf."""
    if isinstance(value, dict):
        return {key: _synthetic(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_synthetic(item) for item in value]
    if isinstance(value, str):
        return "x" * len(value)
    return value


def _browser(size: int) -> str:
    state = {
        "cookies": [
            {
                "name": "synthetic-session",
                "value": "",
                "domain": "example.invalid",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }
    baseline = json.dumps(state, separators=(",", ":"))
    if size < len(baseline.encode()):
        message = "browser fixture is smaller than its schema"
        raise ValueError(message)
    state["cookies"][0]["value"] = "x" * (size - len(baseline.encode()))
    return json.dumps(state, separators=(",", ":"))


def _payload(recipe: dict[str, Any]) -> str | bytes | dict[str, Any]:
    kind = recipe["kind"]
    if kind == "text":
        line = recipe["line"]
        return cast(
            "str", (line * (recipe["bytes"] // len(line) + 1))[: recipe["bytes"]]
        )
    if kind == "browser":
        return _browser(recipe["bytes"])
    if kind == "bytes":
        return b"x" * int(recipe["bytes"])
    if kind == "report":
        return cast("dict[str, Any]", recipe["value"])
    message = f"unknown payload recipe: {kind}"
    raise ValueError(message)


def _encoded(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, dict):
        return (
            json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
        ).encode()
    return cast("str", value).encode()


def _save_fixtures(root: Path, output: Path) -> None:
    contracts = _contracts(root)
    report = _synthetic(json.loads((root / _INPUTS["report"]).read_text()))
    recipes = [
        {
            "name": "oauth-token",
            "consumer": "oauth",
            "kind": "text",
            "line": "synthetic-token-",
            "bytes": 128,
        },
        {
            "name": "import-config",
            "consumer": "config",
            "kind": "text",
            "line": "# synthetic import-policy configuration\n",
            "bytes": contracts["config_input"]["bytes"],
        },
        {
            "name": "capture-report",
            "consumer": "report",
            "kind": "report",
            "value": report,
        },
    ]
    recipes.extend(
        {
            "name": f"browser-write-{size}",
            "consumer": "hosted",
            "kind": "browser",
            "bytes": size,
        }
        for size in (4096, 65536, 1 << 20, (4 << 20) - 128)
    )
    recipes.extend(
        {
            "name": f"browser-land-65536-peers-{peers}",
            "consumer": "hosted",
            "kind": "browser",
            "bytes": 65536,
            "peers": peers,
        }
        for peers in (0, 64, 512)
    )
    recipes.extend(
        {
            "name": f"notebook-checkpoint-{size}",
            "consumer": "notebook",
            "kind": "bytes",
            "bytes": size,
        }
        for size in (4096, 1 << 20, 18 << 20)
    )
    for recipe in recipes:
        raw = _encoded(_payload(recipe))
        recipe.update(encoded_bytes=len(raw), encoded_sha256=_sha(raw))
    fixture = {
        "schema": 1,
        "provenance": contracts,
        "synthetic": True,
        "notes": [
            "Payload sizes are sensitivity scenarios, not measured production frequencies.",
            "Report keys/container schema come from the bundled fixture; string leaf values are synthetic.",
            "Report serialization uses shared stdlib-compatible JSON; actual jsonx codec latency is not measured.",
            "Hosted legacy resets mode to 0600; candidates preserve existing mode. All benchmark destinations are 0600.",
            "Notebook candidates use strict durability with an extra preflight sync; these are migration experiments, not transparent replacements.",
        ],
        "cases": recipes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")


def _load_fixtures(path: Path, billy: Path) -> dict[str, Any]:
    fixture: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("schema") != 1 or fixture.get("synthetic") is not True:
        message = "unsupported or nonsynthetic fs fixtures"
        raise ValueError(message)
    if fixture["provenance"] != _contracts(billy):
        message = "Billy consumer source drifted from the captured fixtures"
        raise ValueError(message)
    for recipe in fixture["cases"]:
        encoded = _encoded(_payload(recipe))
        if (
            len(encoded) != recipe["encoded_bytes"]
            or _sha(encoded) != recipe["encoded_sha256"]
        ):
            message = f"corrupt fs payload: {recipe['name']}"
            raise ValueError(message)
    return fixture


def _load_native(path: Path) -> tuple[ModuleType, dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("_fs_experiment", path)
    if spec is None or not isinstance(spec.loader, ExtensionFileLoader):
        message = "native extension must be a compiled _fs_experiment module"
        raise ValueError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    method = module.atomic_write
    if not inspect.isbuiltin(method) or method.__module__ != "_fs_experiment":
        message = "atomic_write is not the selected native entrypoint"
        raise RuntimeError(message)
    return module, {
        "path": str(path.resolve()),
        "sha256": _sha(path.read_bytes()),
        "method": repr(method),
        "active_builtin": True,
    }


def _compiled(
    billy: Path, symbol: str, namespace: dict[str, Any], *, candidate: bool
) -> Callable[..., Any]:
    relative, name = _CONSUMERS[symbol]
    _source, node = _symbol(billy / relative, name)
    if candidate:
        blocks = [
            index for index, item in enumerate(node.body) if isinstance(item, ast.With)
        ]
        if len(blocks) != 1:
            message = "hosted atomic-publication block changed"
            raise ValueError(message)
        node.body[blocks[0]] = ast.parse("_writer(path, encoded)").body[0]
    tree = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(tree, str(billy / relative), "exec"), namespace)  # noqa: S102 — checked local source function only
    return cast("Callable[..., Any]", namespace[name])


@dataclass
class Prepared:
    """One common mutation path with independent expected bytes and mode."""

    recipe: dict[str, Any]
    path: Path
    expected: bytes
    calls: dict[str, Callable[[], Any]]

    def setup(self, _label: str) -> None:
        """Reset outside timing so a declined or no-op writer cannot pass."""
        self.path.write_bytes(b"uncommitted-state-sentinel")
        self.path.chmod(0o600)

    def check(self, _label: str, result: Any) -> None:
        """Reject wrong outputs, broadened modes, and retained scratch files."""
        assert result == self.path
        assert self.path.read_bytes() == self.expected
        assert stat.S_IMODE(self.path.stat().st_mode) == 0o600
        assert set(self.path.parent.iterdir()) == {self.path}


def _hosted_result(function: Callable[..., Any], value: Any) -> Path:
    return Path(function("target", value))


def _prepare(
    fixture: dict[str, Any],
    billy: Path,
    root: Path,
    python: ModuleType,
    native: ModuleType | None,
) -> list[Prepared]:
    writers: dict[str, Callable[..., Any]] = {"python": python.atomic_write}
    if native is not None:

        def native_write(
            path: Path, value: Any, *, durable: bool | str = False
        ) -> Path:
            result = native.atomic_write(path, _encoded(value), durable=durable)
            if result is not None:
                message = "native atomic_write declined its admitted workload"
                raise RuntimeError(message)
            return path

        writers["native"] = native_write
    prepared = []
    for recipe in fixture["cases"]:
        value = _payload(recipe)
        expected = _encoded(value)
        profiles = root / recipe["name"]
        path = profiles / "target" / "state.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"previous")
        path.chmod(0o600)
        for index in range(recipe.get("peers", 0)):
            peer = profiles / f"peer-{index}" / "state.json"
            peer.parent.mkdir()
            peer.write_bytes(b"x" * 128)
        calls: dict[str, Callable[[], Any]] = {}
        for label, writer in writers.items():

            def operation(
                writer: Callable[..., Any] = writer,
                value: Any = value,
                recipe: dict[str, Any] = recipe,
                path: Path = path,
            ) -> Path:
                payload = (
                    json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
                    if recipe["kind"] == "report"
                    else value
                )
                if recipe["consumer"] == "hosted":
                    payload = payload.encode()
                result = writer(
                    path,
                    payload,
                    durable="strict" if recipe["consumer"] == "notebook" else False,
                )
                if recipe["consumer"] == "oauth":
                    path.chmod(0o600)
                return cast("Path", result)

            calls[label] = operation
        if "peers" in recipe:
            for label in list(calls):
                writer = writers[label]
                namespace = {
                    "Path": Path,
                    "os": os,
                    "tempfile": tempfile,
                    "profiles_root": lambda profiles=profiles: profiles,
                    "state_path": lambda _user, path=path: path,
                    "_STATE_MAX_BYTES": 4 << 20,
                    "_STATE_CACHE_BYTES": 64 << 20,
                    "_writer": writer,
                }
                function = _compiled(billy, "hosted", namespace, candidate=True)
                calls[label] = partial(_hosted_result, function, value)
            namespace = {
                "Path": Path,
                "os": os,
                "tempfile": tempfile,
                "profiles_root": lambda profiles=profiles: profiles,
                "state_path": lambda _user, path=path: path,
                "_STATE_MAX_BYTES": 4 << 20,
                "_STATE_CACHE_BYTES": 64 << 20,
            }
            function = _compiled(billy, "hosted", namespace, candidate=False)
            calls["billy"] = partial(_hosted_result, function, value)
        elif recipe["consumer"] == "notebook":
            function = _compiled(
                billy,
                "notebook",
                {"Path": Path, "os": os, "tempfile": tempfile},
                candidate=False,
            )

            def notebook(
                function: Callable[..., Any] = function,
                path: Path = path,
                value: Any = value,
            ) -> Path:
                function(path, value)
                return path

            calls["billy"] = notebook
        prepared.append(Prepared(recipe, path, expected, calls))
    return prepared


def _measure(prepared: Prepared, *, rounds: int, repetitions: int) -> dict[str, Any]:
    case = _BASE.Case(
        prepared.recipe["name"],
        prepared.calls,
        prepared.check,
        prepared.setup,
        "shared destination; untimed sentinel reset before every batch/profile; real fsync; private existing 0600 file",
    )
    result = cast(
        "dict[str, Any]", _BASE._measure(case, rounds=rounds, repetitions=repetitions)
    )
    result.pop("before_over_current")
    medians = result["median_per_operation_ns"]
    result["python_over_native"] = (
        {
            metric: medians["python"][metric] / medians["native"][metric]
            for metric in ("wall_ns", "process_cpu_ns")
        }
        if "native" in medians
        else {}
    )
    result["recipe"] = prepared.recipe
    return result


def main(argv: list[str] | None = None) -> None:
    """Capture recipes or compare Linux Python/native implementations."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("fixtures")
    capture.add_argument("--billy-source", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--fixtures", type=Path, required=True)
    run.add_argument("--billy-source", type=Path, required=True)
    run.add_argument("--source", type=Path, default=_ROOT / "src")
    run.add_argument("--native-extension", type=Path)
    run.add_argument("--python-only", action="store_true")
    run.add_argument("--root", type=Path)
    run.add_argument("--rounds", type=_BASE._positive, default=9)
    run.add_argument("--repetitions", type=_BASE._positive, default=3)
    run.add_argument("--include", action="append")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "fixtures":
        _save_fixtures(args.billy_source, args.output)
        return
    if sys.platform != "linux":
        parser.error("performance execution requires Linux")
    if args.python_only == (args.native_extension is not None):
        parser.error("select --python-only or --native-extension")
    fixture = _load_fixtures(args.fixtures, args.billy_source)
    python, evidence = _BASE._load_source(args.source)
    native, native_evidence = (
        _load_native(args.native_extension)
        if args.native_extension is not None
        else (None, None)
    )
    helper_file = _BASE.__file__
    if helper_file is None:
        message = "filesystem timing helper has no source file"
        raise RuntimeError(message)
    hashes = {
        str(path): _sha(path.read_bytes())
        for path in (Path(__file__), Path(helper_file), args.fixtures)
    }
    report = {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "python_source": evidence,
        "native": native_evidence,
        "fixtures": fixture,
        "harness_files_sha256": hashes,
        "workloads": [],
    }
    with tempfile.TemporaryDirectory(
        prefix="unstd-billy-fs-", dir=args.root
    ) as temporary:
        root = Path(temporary)
        report["environment"].update(
            fixture_root=str(root), fixture_device=root.stat().st_dev
        )
        for prepared in _prepare(fixture, args.billy_source, root, python, native):
            if args.include and prepared.recipe["name"] not in args.include:
                continue
            result = _measure(
                prepared, rounds=args.rounds, repetitions=args.repetitions
            )
            report["workloads"].append(result)
            print(result["name"], result["python_over_native"], flush=True)
    if not report["workloads"]:
        parser.error("workload selection is empty")
    _BASE._check_source_hashes({"python": evidence})
    _load_fixtures(args.fixtures, args.billy_source)
    for filename, expected in hashes.items():
        if _sha(Path(filename).read_bytes()) != expected:
            message = f"benchmark input changed during measurement: {filename}"
            raise RuntimeError(message)
    if (
        native_evidence is not None
        and _sha(Path(native_evidence["path"]).read_bytes())
        != native_evidence["sha256"]
    ):
        message = "native binary changed during measurement"
        raise RuntimeError(message)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
