"""Complete captured decoder qualification with generic validated numeric frames.

The candidate replaces duplicate numeric admission and copies with a configured
RaggedDecoder, then allocates the same captured application result class. Caller
geometry, publication-budget and identity checks remain inside each timed call.
Every call validates its payload; no previous result or validation is reused.

An enforced target failure still writes the complete evidence before exiting::

    python bench/profiles/ragged.py --capture .local/pack-consumer-capture.json \
        --source src --require-native --target-speedup 100 --enforce-target \
        --output /tmp/ragged.json
"""

from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import fields
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from importlib import import_module, util
import json
import math
from pathlib import Path
import platform
from statistics import median
import struct
import sys
from types import ModuleType
from typing import Any


_ROOT = Path(__file__).resolve().parents[2]
_HELPER = Path(__file__).with_name("pack_consumers.py")
_SPEC = util.spec_from_file_location("_ragged_captured_profile", _HELPER)
assert _SPEC is not None
assert _SPEC.loader is not None
profile = util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = profile
_SPEC.loader.exec_module(profile)


def _candidate(
    capture: dict[str, Any], baseline: ModuleType, decoder: Any
) -> ModuleType:
    """Keep caller guards and consume a fully validated immutable frame."""
    cls = baseline.TokenIndex
    if not cls.__dataclass_params__.frozen or tuple(
        field.name for field in fields(cls)
    ) != ("tokens", "offsets", "identity", "_device"):
        message = "captured result class no longer has the reviewed frozen fields"
        raise ValueError(message)
    tree = ast.parse(capture["definitions"]["TokenIndex"])
    post = next(
        node
        for node in tree.body[0].body
        if isinstance(node, ast.FunctionDef) and node.name == "__post_init__"
    )
    messages = [
        node.value.value
        for node in ast.walk(post)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and any(
            isinstance(target, ast.Name) and target.id == "message"
            for target in node.targets
        )
    ]
    expected = ast.parse(
        """
self.tokens.dtype != np.float32 or self.tokens.ndim != 2 or self.tokens.shape[1] != _DIMENSION or not self.tokens.flags.c_contiguous or self.offsets.dtype != np.int64 or self.offsets.ndim != 1 or not len(self.offsets) or self.offsets[0] != 0 or self.offsets[-1] != len(self.tokens) or (np.diff(self.offsets) <= 0).any() or not np.isfinite(self.tokens).all() or not self.identity
""",
        mode="eval",
    ).body
    if (
        len(messages) != 1
        or len(post.body) != 3
        or not isinstance(post.body[0], ast.If)
        or ast.dump(post.body[0].test) != ast.dump(expected)
    ):
        message = (
            "captured numeric admission contract differs from the reviewed migration"
        )
        raise ValueError(message)
    snapshots = ast.parse("""
object.__setattr__(self, 'tokens', np.frombuffer(self.tokens.tobytes(), dtype=np.float32).reshape(self.tokens.shape))
object.__setattr__(self, 'offsets', np.frombuffer(self.offsets.tobytes(), dtype=np.int64))
""").body
    rejection = ast.parse("""
message = ''
raise ValueError(message)
""").body
    assert isinstance(rejection[0], ast.Assign)
    rejection[0].value = ast.Constant(messages[0])
    reviewed = ast.Module(
        body=[ast.If(test=expected, body=rejection, orelse=[]), *snapshots],
        type_ignores=[],
    )
    if ast.dump(ast.Module(body=post.body, type_ignores=[])) != ast.dump(reviewed):
        message = "captured admission or snapshot statements differ from the reviewed migration"
        raise ValueError(message)
    original = ast.parse(capture["definitions"]["_remote_index"]).body[0]
    if (
        not isinstance(original, ast.FunctionDef)
        or len(original.body) != 3
        or not all(isinstance(node, ast.If) for node in original.body[:2])
        or not isinstance(original.body[-1], ast.Return)
    ):
        message = "captured decoder no longer has exactly two caller guards"
        raise ValueError(message)
    replacement = ast.parse("""
try:
    tokens, offsets = decoder.unpack(body, index_count=2)
except ValueError:
    raise ValueError(_INVALID) from None
if not _IDENTITY:
    raise ValueError(_INVALID)
result = object.__new__(TokenIndex)
object.__setattr__(result, 'tokens', tokens)
object.__setattr__(result, 'offsets', offsets)
object.__setattr__(result, 'identity', _IDENTITY)
object.__setattr__(result, '_device', None)
return result
""").body
    migrated = copy.deepcopy(original)
    migrated.body = [*migrated.body[:2], *replacement]
    module = ModuleType("_captured_ragged_candidate")
    module.__dict__.update(
        TokenIndex=cls,
        decoder=decoder,
        _DIMENSION=capture["dimension"],
        _IDENTITY=capture["identity"],
        _INVALID=messages[0],
    )
    sys.modules[module.__name__] = module
    compiled = ast.Module(body=[migrated], type_ignores=[])
    ast.fix_missing_locations(compiled)
    exec(compile(compiled, capture["source_path"], "exec"), module.__dict__)  # noqa: S102 — inspected captured source and explicit adapter
    return module


def _check(actual: Any, fixture: Any, cls: Any, *, shared: bool) -> None:
    """Require the same class, immutable storage, ownership and all output values."""
    if (
        type(actual) is not cls
        or actual._device is not None
        or actual.device_nbytes != 0
    ):
        message = "decoder changed the application result class or device state"
        raise AssertionError(message)
    profile._check_index(actual, fixture)
    profile._check_leaf((actual.tokens, actual.offsets), fixture, shared=shared)


def _admission(modules: dict[str, ModuleType], fixture: Any) -> dict[str, Any]:
    """Check unchanged caller and generic numeric failures, including late values."""
    observed: dict[str, Any] = profile._admission(modules, fixture)
    cases = {
        "first-boundary": struct.pack("<2q", 1, fixture.rows) + fixture.tokens,
        **{
            name: fixture.body[:-4] + struct.pack("<f", value)
            for name, value in (
                ("late-nan", float("nan")),
                ("late-positive-infinity", float("inf")),
                ("late-negative-infinity", -float("inf")),
            )
        },
    }
    for side, module in modules.items():
        for case, body in cases.items():
            try:
                module._remote_index(body, len(fixture.tokens))
            except ValueError as error:
                observed[side][case] = {
                    "exception": type(error).__name__,
                    "message": str(error),
                }
            else:
                message = f"{side} decoder admitted {case}"
                raise AssertionError(message)
        namespace = vars(module)
        identity = namespace["_IDENTITY"]
        namespace["_IDENTITY"] = ""
        try:
            try:
                module._remote_index(fixture.body, len(fixture.tokens))
            except ValueError as error:
                observed[side]["empty-identity"] = {
                    "exception": type(error).__name__,
                    "message": str(error),
                }
            else:
                message = f"{side} decoder admitted an empty identity"
                raise AssertionError(message)
        finally:
            namespace["_IDENTITY"] = identity
    if observed["baseline"] != observed["candidate"]:
        message = "generic decoding changed captured admission errors"
        raise AssertionError(message)
    return observed


def _native_proof(ragged: Any, candidate: ModuleType, fixture: Any) -> dict[str, Any]:
    """Observe native calls outside timing, including repeated and late-invalid data."""
    native = ragged._native
    if native is None:
        return {"available": False, "validation_calls": []}
    original = native.validate
    calls = []

    def recorded(*args: Any) -> int:
        calls.append(
            {
                "bytes": len(args[0]),
                "index_count": args[1],
                "columns": args[2],
                "value_char": args[3],
                "value_order": args[4],
                "index_char": args[5],
                "index_order": args[6],
                "finite": args[7],
                "allow_empty": args[8],
            }
        )
        return int(original(*args))

    native.validate = recorded
    try:
        for _ in range(2):
            candidate._remote_index(fixture.body, len(fixture.tokens))
        bad = fixture.body[:-4] + struct.pack("<f", float("nan"))
        try:
            candidate._remote_index(bad, len(fixture.tokens))
        except ValueError:
            pass
        else:
            message = "native execution proof admitted a late NaN"
            raise AssertionError(message)
    finally:
        native.validate = original
    if len(calls) != 3 or any(
        call["finite"] is not True or call["allow_empty"] is not False for call in calls
    ):
        message = "complete decoder did not run configured native validation each time"
        raise AssertionError(message)
    return {
        "available": True,
        "KERNEL_VERSION": native.KERNEL_VERSION,
        "binary": native.__file__,
        "validation_calls": calls,
    }


def _fingerprints(source: Path, native: Any) -> dict[str, str]:
    """Bind Python, native inputs, binary and both harness implementations."""
    result: dict[str, str] = profile._source_hashes(source)
    paths = [Path(__file__), _HELPER]
    paths.extend(
        path
        for path in sorted((_ROOT / "native/pack/src").glob("*"))
        if path.suffix in {".c", ".h"}
    )
    if native is not None:
        paths.append(Path(native.__file__))
    result.update(
        {
            str(path.resolve()): sha256(path.read_bytes()).hexdigest()
            for path in paths
            if path.is_file()
        }
    )
    return result


def _summary(samples: dict[str, list[dict[str, Any]]], target: float) -> dict[str, Any]:
    before = [sample["wall_ns_per_call"] for sample in samples["baseline"]]
    after = [sample["wall_ns_per_call"] for sample in samples["candidate"]]
    conservative = min(before) / max(after)
    speedup = median(before) / median(after)
    return {
        "baseline_median_wall_ns": median(before),
        "candidate_median_wall_ns": median(after),
        "median_wall_speedup": speedup,
        "observed_conservative_wall_speedup": conservative,
        "median_cpu_speedup": median(
            [sample["cpu_ns_per_call"] for sample in samples["baseline"]]
        )
        / median([sample["cpu_ns_per_call"] for sample in samples["candidate"]]),
        "target_speedup": target,
        "median_target_met": speedup >= target,
        "target_met": conservative >= target,
        "samples": samples,
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    if sys.byteorder != "little":
        message = "the captured caller requires native little-endian arrays"
        raise RuntimeError(message)
    if any(name == "unstd" or name.startswith("unstd.") for name in sys.modules):
        message = "product was imported before source selection"
        raise RuntimeError(message)
    sys.path.insert(0, str(source))
    codec = import_module("unstd.pack.binpack")
    ragged = import_module("unstd.pack.ragged")
    if (
        ragged.__file__ is None
        or codec.__file__ is None
        or Path(ragged.__file__).resolve() != source / "unstd/pack/ragged.py"
        or Path(codec.__file__).resolve() != source / "unstd/pack/binpack.py"
    ):
        message = "selected packing source was not imported"
        raise RuntimeError(message)
    if args.require_native and ragged._native is None:
        message = "complete decoder qualification requires the native validator"
        raise RuntimeError(message)
    if ragged._native is not None and (
        ragged._native.__file__ is None
        or Path(ragged._native.__file__).resolve().parent != source / "unstd/pack"
    ):
        message = "native validator was not imported from the selected source"
        raise RuntimeError(message)
    fingerprints = _fingerprints(source, ragged._native)
    capture = profile._read_capture(args.capture)
    baseline = profile._compile_consumers(capture, codec)["baseline"]
    decoder = ragged.RaggedDecoder(
        "<f4",
        columns=capture["dimension"],
        index_dtype="<i8",
        finite=True,
        allow_empty=False,
    )
    modules = {
        "baseline": baseline,
        "candidate": _candidate(capture, baseline, decoder),
    }
    small = profile._fixture(min(args.rows), capture)
    admission = _admission(modules, small)
    native = _native_proof(ragged, modules["candidate"], small)
    results = {}
    for rows in args.rows:
        fixture = profile._fixture(rows, capture)
        samples: dict[str, list[dict[str, Any]]] = {side: [] for side in modules}
        for index in range(args.rounds):
            for side in (
                ("baseline", "candidate")
                if index % 2 == 0
                else ("candidate", "baseline")
            ):
                samples[side].append(
                    profile._measure(
                        partial(
                            modules[side]._remote_index,
                            fixture.body,
                            len(fixture.tokens),
                        ),
                        partial(
                            _check,
                            fixture=fixture,
                            cls=baseline.TokenIndex,
                            shared=side == "candidate",
                        ),
                        int(args.batch_ms * 1_000_000),
                    )
                )
        results[f"complete-decoder/{rows}x{capture['dimension']}"] = {
            "wire_bytes": len(fixture.body),
            **_summary(samples, args.target_speedup),
        }
    if fingerprints != _fingerprints(source, ragged._native):
        message = "qualification sources or native binary changed during measurement"
        raise RuntimeError(message)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "numpy_version": vars(codec)["np"].__version__,
        "source": str(source),
        "fingerprints": fingerprints,
        "capture_sha256": capture["capture_sha256"],
        "rounds": args.rounds,
        "native": native,
        "admission_checks": admission,
        "measurement": "complete validation, array creation, exact application class allocation and refcount cleanup included; immutable fixture reused but no validation/result cache; setup and oracles excluded; GC disabled during batches",
        "target_speedup": args.target_speedup,
        "all_targets_met": all(row["target_met"] for row in results.values()),
        "results": results,
    }


def main() -> int:
    """Save full evidence even when the explicit complete-decoder target fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--batch-ms", type=float, default=50)
    parser.add_argument("--rows", type=int, nargs="+", default=[128, 8192, 65536])
    parser.add_argument("--require-native", action="store_true")
    parser.add_argument("--target-speedup", type=float, default=100)
    parser.add_argument("--enforce-target", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        args.rounds < 1
        or not math.isfinite(args.batch_ms)
        or args.batch_ms <= 0
        or not math.isfinite(args.target_speedup)
        or args.target_speedup <= 0
        or any(rows < 1 for rows in args.rows)
    ):
        parser.error("rounds, durations, targets and row counts must be positive")
    result = _run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for case, row in result["results"].items():
        print(
            f"{case}: {row['baseline_median_wall_ns'] / 1000:.3f} -> {row['candidate_median_wall_ns'] / 1000:.3f} us ({row['median_wall_speedup']:.3f}x); target_met={row['target_met']}"
        )
    return int(args.enforce_target and not result["all_targets_met"])


if __name__ == "__main__":
    raise SystemExit(main())
