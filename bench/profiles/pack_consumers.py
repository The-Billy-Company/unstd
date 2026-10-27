"""Captured caller qualification for array decoding without payload slices.

Capture the real consumer once, then replay exactly those saved source bytes on
each operating system::

    python bench/profiles/pack_consumers.py --consumer-root /path/to/consumer \
        --write-capture /tmp/pack-consumer.json
    python bench/profiles/pack_consumers.py --capture /tmp/pack-consumer.json \
        --source src --rounds 7 --output /tmp/pack-consumers.json

The candidate changes only the two sliced ``frombuffer`` calls in the captured
decoder. Complete-call cases keep the original admission and immutable array
snapshots. Separate leaf cases exclude those operations and are labeled as such.
Neither measurement includes transport or model inference.
"""

from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
import gc
from hashlib import sha256
import importlib
import json
from pathlib import Path
import platform
from statistics import median
import struct
import sys
import time
from types import ModuleType
from typing import TYPE_CHECKING, Any, override


if TYPE_CHECKING:
    from collections.abc import Callable


_ROOT = Path(__file__).resolve().parents[2]
_CONSUMER = "services/ai/core/foundation/llm/local/colbert.py"


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _node_source(source: str, node: ast.ClassDef | ast.FunctionDef) -> str:
    """Retain exact source text, including class decorators."""
    decorators = getattr(node, "decorator_list", [])
    start = min([node.lineno, *[item.lineno for item in decorators]])
    return "".join(source.splitlines(keepends=True)[start - 1 : node.end_lineno])


def _capture(consumer_root: Path) -> dict[str, Any]:
    """Capture two actual consumer definitions and their governing literals."""
    path = consumer_root / _CONSUMER
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in {"TokenIndex", "_remote_index"}
    }
    if set(selected) != {"TokenIndex", "_remote_index"}:
        message = "the captured source lacks the expected consumer definitions"
        raise ValueError(message)
    dimensions = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_DIMENSION"
        and isinstance(node.value, ast.Constant)
    ]
    if len(dimensions) != 1 or type(dimensions[0]) is not int or dimensions[0] <= 0:
        message = "the consumer dimension is not one positive source literal"
        raise ValueError(message)
    pins = path.with_suffix(".json").read_text(encoding="utf-8")
    identity = json.loads(pins)["identity"]
    if type(identity) is not str or not identity:
        message = "the consumer identity is absent from its pinned configuration"
        raise ValueError(message)
    payload = {
        "schema": 1,
        "source_path": _CONSUMER,
        "source_sha256": _digest(source),
        "pins_sha256": _digest(pins),
        "dimension": dimensions[0],
        "identity": identity,
        "definitions": {
            name: _node_source(source, node) for name, node in selected.items()
        },
        "scope": "TokenIndex and _remote_index only; no transport or inference",
    }
    # Confirm that the intended migration is still exactly the two array calls.
    _transformed(payload)
    return {**payload, "capture_sha256": _digest(_canonical(payload))}


def _read_capture(path: Path) -> dict[str, Any]:
    """Reject edited or structurally incompatible retained evidence."""
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    expected = payload.pop("capture_sha256")
    if payload.get("schema") != 1 or expected != _digest(_canonical(payload)):
        message = "consumer capture digest or schema differs"
        raise ValueError(message)
    _transformed(payload)
    return {**payload, "capture_sha256": expected}


class _WindowCalls(ast.NodeTransformer):
    """Change only the decoder's two exact sliced array-selection expressions."""

    def __init__(self) -> None:
        self.changes = 0

    @override
    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if not (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "np"
            and node.func.attr == "frombuffer"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Subscript)
            and isinstance(node.args[0].value, ast.Name)
            and node.args[0].value.id == "body"
            and isinstance(node.args[0].slice, ast.Slice)
            and len(node.keywords) == 1
            and node.keywords[0].arg == "dtype"
            and isinstance(node.keywords[0].value, ast.Constant)
        ):
            return node
        window = node.args[0].slice
        dtype = node.keywords[0].value.value
        if window.step is not None:
            return node
        if (
            dtype == "<f4"
            and isinstance(window.lower, ast.Constant)
            and window.lower.value == 16
            and window.upper is None
        ):
            keywords = [ast.keyword(arg="offset", value=ast.Constant(16))]
        elif (
            dtype == "<i8"
            and window.lower is None
            and isinstance(window.upper, ast.Constant)
            and window.upper.value == 16
        ):
            keywords = [ast.keyword(arg="count", value=ast.Constant(2))]
        else:
            return node
        self.changes += 1
        selected = ast.Call(
            func=ast.Attribute(
                value=ast.Name(id="binpack", ctx=ast.Load()),
                attr="unpack_array",
                ctx=ast.Load(),
            ),
            args=[ast.Constant(dtype), ast.Name(id="body", ctx=ast.Load())],
            keywords=keywords,
        )
        return ast.copy_location(
            ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id="np", ctx=ast.Load()),
                    attr="asarray",
                    ctx=ast.Load(),
                ),
                args=[selected],
                keywords=[ast.keyword(arg="dtype", value=ast.Constant(dtype))],
            ),
            node,
        )


def _transformed(capture: dict[str, Any]) -> tuple[ast.Module, ast.Module]:
    """Keep every captured statement except the two reviewed call replacements."""
    definitions = capture["definitions"]
    if set(definitions) != {"TokenIndex", "_remote_index"}:
        message = "consumer capture contains unexpected definitions"
        raise ValueError(message)
    source = "from __future__ import annotations\n" + "\n".join(
        definitions[name] for name in ("TokenIndex", "_remote_index")
    )
    before = ast.parse(source)
    after = copy.deepcopy(before)
    decoder = next(
        node
        for node in after.body
        if isinstance(node, ast.FunctionDef) and node.name == "_remote_index"
    )
    transform = _WindowCalls()
    transform.visit(decoder)
    if transform.changes != 2:
        message = f"consumer migration changed {transform.changes} calls instead of two"
        raise ValueError(message)
    ast.fix_missing_locations(after)
    return before, after


def _leaf(tree: ast.Module) -> ast.FunctionDef:
    """Extract only the actual return expression's two numeric input selections."""
    decoder = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_remote_index"
    )
    result = decoder.body[-1]
    if not (
        isinstance(result, ast.Return)
        and isinstance(result.value, ast.Call)
        and isinstance(result.value.func, ast.Name)
        and result.value.func.id == "TokenIndex"
        and len(result.value.args) == 3
    ):
        message = "consumer return no longer constructs the captured TokenIndex"
        raise ValueError(message)
    leaf = ast.parse("def _leaf(body):\n    return ()\n").body[0]
    assert isinstance(leaf, ast.FunctionDef)
    leaf.body = [
        ast.Return(
            value=ast.Tuple(elts=copy.deepcopy(result.value.args[:2]), ctx=ast.Load())
        )
    ]
    return leaf


def _compile_consumers(capture: dict[str, Any], codec: Any) -> dict[str, ModuleType]:
    """Run saved consumer source with the actual numerical backend supplied."""
    import numpy as np

    before, after = _transformed(capture)
    modules = {}
    for name, tree in (("baseline", before), ("candidate", after)):
        tree.body.append(_leaf(tree))
        ast.fix_missing_locations(tree)
        module = ModuleType(f"_captured_pack_{name}")
        module.__dict__.update(
            np=np,
            dataclass=dataclass,
            field=field,
            binpack=codec,
            _DIMENSION=capture["dimension"],
            _IDENTITY=capture["identity"],
        )
        sys.modules[module.__name__] = module
        code = compile(tree, capture["source_path"], "exec")
        exec(code, module.__dict__)  # noqa: S102 — retained, inspected local consumer definitions
        modules[name] = module
    return modules


@dataclass(frozen=True)
class Fixture:
    """A wire buffer whose exact numeric representation comes from struct."""

    rows: int
    dimension: int
    body: bytes
    tokens: bytes
    identity: str


def _fixture(rows: int, capture: dict[str, Any]) -> Fixture:
    dimension = capture["dimension"]
    row = struct.pack(
        f"<{dimension}f", *[(index % 31 - 15) / 32 for index in range(dimension)]
    )
    tokens = row * rows
    return Fixture(
        rows,
        dimension,
        struct.pack("<2q", 0, rows) + tokens,
        tokens,
        capture["identity"],
    )


def _check_arrays(tokens: Any, offsets: Any, fixture: Fixture) -> None:
    """Require the complete array values, shape, type and immutable byte backing."""
    import numpy as np

    if (
        type(tokens) is not np.ndarray
        or tokens.dtype != np.dtype("<f4")
        or tokens.shape != (fixture.rows, fixture.dimension)
        or not tokens.flags.c_contiguous
        or tokens.flags.writeable
        or tokens.tobytes() != fixture.tokens
        or type(offsets) is not np.ndarray
        or offsets.dtype != np.dtype("<i8")
        or offsets.shape != (2,)
        or offsets.flags.writeable
        or offsets.tolist() != [0, fixture.rows]
    ):
        message = "consumer numeric result differs from the independent struct oracle"
        raise AssertionError(message)
    for value in (tokens, offsets):
        owner: Any = value
        while isinstance(owner, np.ndarray):
            owner = owner.base
        if type(owner) is not bytes:
            message = "consumer result lacks immutable bytes backing"
            raise AssertionError(message)


def _check_index(actual: Any, fixture: Fixture) -> None:
    _check_arrays(actual.tokens, actual.offsets, fixture)
    if actual.identity != fixture.identity or actual.nbytes != len(fixture.body):
        message = "consumer identity or retained numeric size differs"
        raise AssertionError(message)


def _check_leaf(actual: Any, fixture: Fixture, *, shared: bool) -> None:
    import numpy as np

    if type(actual) is not tuple or len(actual) != 2:
        message = "numeric selection result differs from the captured expression"
        raise AssertionError(message)
    _check_arrays(*actual, fixture)
    source = np.frombuffer(fixture.body, dtype="u1")
    if any(bool(np.shares_memory(value, source)) != shared for value in actual):
        message = "numeric selection copied or aliased unexpected source storage"
        raise AssertionError(message)


def _batch(run: Callable[[], Any], count: int) -> tuple[int, int]:
    cpu = time.process_time_ns()
    wall = time.perf_counter_ns()
    for _ in range(count):
        run()
    return time.perf_counter_ns() - wall, time.process_time_ns() - cpu


def _admission(modules: dict[str, ModuleType], fixture: Fixture) -> dict[str, Any]:
    """Require the actual decoder to reject malformed or unpublishable payloads."""
    bad_boundary = struct.pack("<2q", 0, fixture.rows + 1) + fixture.tokens
    nonfinite = fixture.body[:16] + struct.pack("<f", float("nan")) + fixture.body[20:]
    failures = {
        "short-header": (b"", len(fixture.tokens)),
        "partial-row": (fixture.body + b"x", len(fixture.tokens) + 1),
        "publication-budget": (fixture.body, len(fixture.tokens) - 1),
        "boundary-mismatch": (bad_boundary, len(fixture.tokens)),
        "nonfinite-token": (nonfinite, len(fixture.tokens)),
    }
    observed: dict[str, dict[str, Any]] = {}
    for name, module in modules.items():
        observed[name] = {}
        for case, (body, remaining) in failures.items():
            try:
                module._remote_index(body, remaining)
            except ValueError as error:
                observed[name][case] = {
                    "exception": type(error).__name__,
                    "message": str(error),
                }
            else:
                message = f"captured {name} decoder admitted {case}"
                raise AssertionError(message)
    if observed["baseline"] != observed["candidate"]:
        message = "migrated decoder changed captured admission errors"
        raise AssertionError(message)
    return observed


def _measure(
    run: Callable[[], Any], check: Callable[[Any], None], target_ns: int
) -> dict[str, Any]:
    """Reject invalid output before calibrating or timing a complete call."""
    check(run())
    count = 1
    enabled = gc.isenabled()
    gc.disable()
    try:
        while True:
            elapsed, _ = _batch(run, count)
            if elapsed >= target_ns // 4 or count >= 1_000_000:
                break
            count *= 2
        count = max(1, min(1_000_000, int(count * target_ns / max(1, elapsed))))
        wall, cpu = _batch(run, count)
    finally:
        if enabled:
            gc.enable()
    check(run())
    return {
        "iterations": count,
        "wall_ns": wall,
        "cpu_ns": cpu,
        "wall_ns_per_call": wall / count,
        "cpu_ns_per_call": cpu / count,
        "oracle_passed": True,
    }


def _source_hashes(source: Path) -> dict[str, str]:
    paths = [source / "unstd" / "__init__.py"]
    paths.extend(sorted((source / "unstd" / "pack").rglob("*.py")))
    return {
        str(path.relative_to(source)): sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Alternate each complete operation against its captured original caller."""
    source = args.source.resolve()
    capture = _read_capture(args.capture)
    hashes = _source_hashes(source)
    if any(name == "unstd" or name.startswith("unstd.") for name in sys.modules):
        message = "product was imported before source selection"
        raise RuntimeError(message)
    sys.path.insert(0, str(source))
    codec = importlib.import_module("unstd.pack.binpack")
    if (
        codec.__file__ is None
        or Path(codec.__file__).resolve() != source / "unstd" / "pack" / "binpack.py"
    ):
        message = "selected pack implementation was not imported"
        raise RuntimeError(message)
    modules = _compile_consumers(capture, codec)
    admission = _admission(modules, _fixture(min(args.rows), capture))
    results = {}
    for rows in args.rows:
        fixture = _fixture(rows, capture)
        for kind in ("complete-decoder", "array-selection-only"):
            operations = {}
            checks = {}
            for name, module in modules.items():
                if kind == "complete-decoder":
                    operations[name] = partial(
                        module._remote_index, fixture.body, len(fixture.tokens)
                    )
                    checks[name] = partial(_check_index, fixture=fixture)
                else:
                    operations[name] = partial(module._leaf, fixture.body)
                    checks[name] = partial(
                        _check_leaf, fixture=fixture, shared=name == "candidate"
                    )
            samples: dict[str, list[dict[str, Any]]] = {name: [] for name in modules}
            for index in range(args.rounds):
                order = (
                    ("baseline", "candidate")
                    if index % 2 == 0
                    else ("candidate", "baseline")
                )
                for name in order:
                    samples[name].append(
                        _measure(
                            operations[name],
                            checks[name],
                            int(args.batch_ms * 1_000_000),
                        )
                    )
            before = [sample["wall_ns_per_call"] for sample in samples["baseline"]]
            after = [sample["wall_ns_per_call"] for sample in samples["candidate"]]
            before_cpu = [sample["cpu_ns_per_call"] for sample in samples["baseline"]]
            after_cpu = [sample["cpu_ns_per_call"] for sample in samples["candidate"]]
            results[f"{kind}/{rows}x{fixture.dimension}"] = {
                "wire_bytes": len(fixture.body),
                "baseline_median_wall_ns": median(before),
                "candidate_median_wall_ns": median(after),
                "median_wall_speedup": median(before) / median(after),
                "observed_conservative_wall_speedup": min(before) / max(after),
                "median_cpu_speedup": median(before_cpu) / median(after_cpu),
                "samples": samples,
            }
    if _source_hashes(source) != hashes:
        message = "pack source changed during caller qualification"
        raise RuntimeError(message)
    import numpy as np

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "numpy_version": np.__version__,
        "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "capture_sha256": capture["capture_sha256"],
        "source": str(source),
        "source_hashes": hashes,
        "rounds": args.rounds,
        "admission_checks": admission,
        "measurement": "operation allocation/refcount cleanup included; setup/oracles excluded; GC disabled during batches; complete decoder retains captured validation and snapshots",
        "candidate_changes": "exactly two sliced frombuffer calls replaced with unpack_array count/offset; all other captured code unchanged",
        "results": results,
    }


def main() -> None:
    """Capture or replay one bounded consumer qualification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer-root", type=Path)
    parser.add_argument("--write-capture", type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--batch-ms", type=float, default=30)
    parser.add_argument("--rows", type=int, nargs="+", default=[128, 8192, 65536])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.write_capture is not None:
        if args.consumer_root is None:
            parser.error("--write-capture requires --consumer-root")
        args.write_capture.parent.mkdir(parents=True, exist_ok=True)
        args.write_capture.write_text(
            json.dumps(_capture(args.consumer_root), indent=2) + "\n", encoding="utf-8"
        )
        return
    if args.capture is None or args.output is None:
        parser.error("--capture and --output are required for replay")
    if args.rounds < 1 or args.batch_ms <= 0 or any(value < 1 for value in args.rows):
        parser.error("rounds, batch duration, and row counts must be positive")
    result = _run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for name, row in result["results"].items():
        print(
            f"{name}: {row['baseline_median_wall_ns'] / 1000:.3f} -> {row['candidate_median_wall_ns'] / 1000:.3f} us ({row['median_wall_speedup']:.3f}x)"
        )


if __name__ == "__main__":
    main()
