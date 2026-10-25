"""Per-function serde profiles and repeatable, output-checked workload timings.

Run ``python -m bench.serde_profile --output /tmp/serde.json``. Compare a saved
source tree with ``--compare-source /path/containing/unstd``. Workers isolate
imports, alternate the source trees between repetitions, and time without the
profiler. Native methods and stdlib aliases are exercised alongside Python
functions; Protocol/overload declarations are excluded from function coverage.
"""

from __future__ import annotations

import argparse
import ast
import cProfile
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
import importlib
from importlib.metadata import version
import inspect
import io
import json
from pathlib import Path
import platform
import pstats
import subprocess
import sys
from typing import TYPE_CHECKING, Any


if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from bench.core.timing import measure


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@dataclass
class Workload:
    """One operation with an independently constructed expected answer."""

    name: str
    call: Callable[[], Any]
    expected: Any


def _failure(call: Callable[[], Any]) -> str:
    try:
        call()
    except (TypeError, ValueError) as error:
        return type(error).__name__
    msg = "expected a codec failure"
    raise AssertionError(msg)


def _same(actual: Any, expected: Any) -> bool:
    """Compare outputs without hiding changed types or dictionary key order."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return list(actual) == list(expected) and all(
            _same(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list | tuple):
        return len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def _workloads() -> list[Workload]:
    import base64

    import msgspec

    from unstd.serde import b64, jsonx, ndjson, structs

    workloads: list[Workload] = []

    def add(name: str, call: Callable[[], Any], expected: Any) -> None:
        workloads.append(Workload(name, call, expected))

    small = {"id": 7, "name": "small", "ok": True, "values": [1, 2]}
    wide = {**small, "values": list(range(32))}
    records = [{"id": i, "text": "café", "score": 1.5} for i in range(500)]
    text = {"text": "abcdef " * 10_000}
    value: Any
    for label, value in (
        ("small", small),
        ("integers8", list(range(8))),
        ("integers15", list(range(15))),
        ("wide", wide),
        ("records", records),
        ("text", text),
        ("string", "hello"),
        ("int", 7),
        ("float", 1.5),
        ("null", None),
    ):
        add(f"jsonx.dumps/{label}", partial(jsonx.dumps, value), json.dumps(value))
        add(
            f"jsonx.dumpb/{label}",
            partial(jsonx.dumpb, value),
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
        )
    for label, options in (
        ("compact", {"separators": (",", ":")}),
        ("indent", {"indent": 2}),
        ("indent4", {"indent": 4}),
        ("sorted", {"sort_keys": True}),
        ("custom", {"default": str}),
        ("class", {"cls": json.JSONEncoder}),
    ):
        add(
            f"jsonx.dumps/{label}",
            partial(jsonx.dumps, small, **options),
            json.dumps(small, **options),
        )
    add(
        "jsonx.canonical/default",
        lambda: jsonx.canonical(small),
        json.dumps(small, sort_keys=True, separators=(",", ":")).encode(),
    )
    add(
        "jsonx.canonical/custom",
        lambda: jsonx.canonical(small, default=None),
        json.dumps(small, sort_keys=True, separators=(",", ":")).encode(),
    )

    def dump() -> str:
        sink = io.StringIO()
        jsonx.dump(small, sink)
        return sink.getvalue()

    add("jsonx.dump/stream", dump, json.dumps(small))
    for label, value in (
        ("small", small),
        ("integers", list(range(1000))),
        ("records", records),
        ("float", 1.5),
        ("string", "hello"),
    ):
        document = json.dumps(value)
        add(f"jsonx.loads/{label}", partial(jsonx.loads, document), value)
        binary_document = document.encode()
        add(
            f"jsonx.loads/{label}/bytes",
            partial(jsonx.loads, binary_document),
            value,
        )
    add("jsonx.loads/hook", lambda: jsonx.loads("[1]", parse_int=str), ["1"])
    add(
        "jsonx.loads/error",
        lambda: _failure(lambda: jsonx.loads("[1,")),
        "JSONDecodeError",
    )
    add("jsonx.load/stream", lambda: jsonx.load(io.StringIO("[1,2]")), [1, 2])
    if jsonx._HAVE_NUMPY:
        import numpy as np

        array = np.array([1, 2])
        scalar = np.int64(1)
        decimal = Decimal("1.5")
        add("jsonx.dumps/numpy", lambda: jsonx.dumps(array, numpy=True), "[1, 2]")
        add(
            "jsonx.dumps/numpy-scalar",
            lambda: jsonx.dumps(scalar, numpy=True),
            "1",
        )
        add(
            "jsonx.dumps/numpy-default",
            lambda: jsonx.dumps(decimal, numpy=True, default=str),
            '"1.5"',
        )
        add(
            "jsonx.dumps/numpy-error",
            lambda: _failure(lambda: jsonx.dumps(object(), numpy=True)),
            "TypeError",
        )

    for size in (0, 16, 128, 1024, 131072):
        raw = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
        encoded = base64.b64encode(raw)
        url = base64.urlsafe_b64encode(raw)
        add(f"b64.b64encode/{size}", partial(b64.b64encode, raw), encoded)
        add(
            f"b64.b64decode/{size}",
            partial(b64.b64decode, encoded, validate=True),
            raw,
        )
        encoded_text = encoded.decode()
        add(
            f"b64.b64decode/text/{size}",
            partial(b64.b64decode, encoded_text, validate=True),
            raw,
        )
        add(f"b64.encode/{size}", partial(b64.encode, raw), encoded.decode())
        add(f"b64.decode/url/{size}", partial(b64.decode, url, url=True), raw)
        add(
            f"b64.decode/url-text/{size}",
            partial(b64.decode, url.decode(), url=True),
            raw,
        )
        add(
            f"b64.decode/url-unpadded-text/{size}",
            partial(b64.decode, url.decode().rstrip("="), url=True),
            raw,
        )
    raw = b"hello"
    for name, expected in (
        ("standard_b64encode", b"aGVsbG8="),
        ("urlsafe_b64encode", b"aGVsbG8="),
        ("encodebytes", b"aGVsbG8=\n"),
    ):
        call = getattr(b64, name)
        add(f"b64.{name}", partial(call, raw), expected)
    for name in ("standard_b64decode", "urlsafe_b64decode", "decodebytes", "decode"):
        call = getattr(b64, name)
        add(f"b64.{name}", partial(call, b"aGVsbG8="), raw)
    add("b64.decode_text", lambda: b64.decode_text("aGVsbG8="), "hello")
    add(
        "b64.encode_json",
        lambda: b64.encode_json(small),
        base64.b64encode(
            json.dumps(small, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode(),
    )
    encoded_json = base64.b64encode(json.dumps(small).encode())
    add("b64.decode_json", lambda: b64.decode_json(encoded_json), small)
    add("b64.decode/error", lambda: _failure(lambda: b64.decode("!")), "Error")
    add("b64.decode/url-dirty", lambda: b64.decode(b"aG!VsbG8", url=True), raw)
    add(
        "b64.decode/url-buffer",
        lambda: b64.decode(memoryview(b"aGVsbG8"), url=True),
        raw,
    )
    for name, data, expected in (
        ("encode_file", raw, b"aGVsbG8=\n"),
        ("decode_file", b"aGVsbG8=\n", raw),
    ):
        call = getattr(b64, name)

        def stream(c: Any = call, d: bytes = data) -> bytes:
            sink = io.BytesIO()
            c(io.BytesIO(d), sink)
            return sink.getvalue()

        add(f"b64.{name}", stream, expected)

    class Row(msgspec.Struct):
        id: int
        name: str

    codec = structs.Codec(Row)
    row = Row(7, "small")
    row_raw = b'{"id":7,"name":"small"}'
    add("structs.Codec/construct", lambda: structs.Codec(Row).encode(row), row_raw)
    add("structs.Codec.encode", lambda: codec.encode(row), row_raw)
    add("structs.Codec.decode", lambda: codec.decode(row_raw), row)
    add("structs.Codec.decode_or/valid", lambda: codec.decode_or(row_raw), row)
    add("structs.Codec.decode_or/invalid", lambda: codec.decode_or(b"!"), None)
    numeric = Decimal("1.5")
    add("structs.float_enc_hook", lambda: structs.float_enc_hook(numeric), 1.5)
    add(
        "structs.float_enc_hook/error",
        lambda: _failure(lambda: structs.float_enc_hook(object())),
        "TypeError",
    )
    add(
        "ndjson.dumpb/typed-single",
        lambda: ndjson.dumpb([row], codec=codec),
        row_raw + b"\n",
    )

    class CustomCodec:
        """A non-native codec exercises the generic typed writer."""

        def encode(self, value: object) -> bytes:
            return json.dumps(value, separators=(",", ":")).encode()

        def decode(self, raw: str | bytes) -> Any:
            return json.loads(raw)

    custom_codec = CustomCodec()
    add(
        "ndjson.dumpb/custom-codec",
        lambda: ndjson.dumpb([{"id": 7}], codec=custom_codec),
        b'{"id":7}\n',
    )

    def iterate(lines: Any, selected_codec: Any) -> Any:
        return list(ndjson.iter_loads(lines, codec=selected_codec))

    def read(blob: bytes, selected_codec: Any) -> Any:
        return ndjson.load(io.BytesIO(blob), codec=selected_codec)

    rows: Any
    selected: Any
    for typed in (False, True):
        selected = codec if typed else None
        rows = [row if typed else {"id": 7, "name": "small"}] * 100
        nd_document = row_raw + b"\n"
        expected_rows = rows
        label = "typed" if typed else "untyped"
        add(
            f"ndjson.dumpb/{label}",
            partial(ndjson.dumpb, rows, codec=selected),
            nd_document * 100,
        )
        add(
            f"ndjson.dumps/{label}",
            partial(ndjson.dumps, rows, codec=selected),
            (nd_document * 100).decode(),
        )
        add(
            f"ndjson.loads/{label}",
            partial(ndjson.loads, nd_document * 100, codec=selected),
            expected_rows,
        )
        add(
            f"ndjson.iter_loads/{label}",
            partial(iterate, [nd_document] * 100, selected),
            expected_rows,
        )
        add(
            f"ndjson.load/{label}",
            partial(read, nd_document * 100, selected),
            expected_rows,
        )
        for text_sink in (False, True):

            def write(r: Any = rows, c: Any = selected, t: bool = text_sink) -> Any:
                sink = io.StringIO() if t else io.BytesIO()
                count = ndjson.dump(r, sink, codec=c)
                return count, sink.getvalue()

            expected_blob = (
                (nd_document * 100).decode() if text_sink else nd_document * 100
            )
            add(
                f"ndjson.dump/{label}/{'text' if text_sink else 'binary'}",
                write,
                (100, expected_blob),
            )
    add(
        "ndjson.loads/error",
        lambda: _failure(lambda: ndjson.loads(b"!\n")),
        "NDJSONDecodeError",
    )
    add("ndjson.loads/skip", lambda: ndjson.loads(b"!\n1\n", skip_errors=True), [1])
    add("ndjson.loads/strip", lambda: ndjson.loads("\v1\v\n"), [1])
    return workloads


def _functions(path: Path) -> Iterator[tuple[int, str]]:
    def walk(node: ast.AST, prefix: str = "") -> Iterator[tuple[int, str]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and ast.unparse(child.test) == "TYPE_CHECKING":
                continue
            if isinstance(child, ast.ClassDef):
                if any("Protocol" in ast.unparse(base) for base in child.bases):
                    continue
                yield from walk(child, prefix + child.name + ".")
            elif isinstance(child, ast.FunctionDef):
                if not prefix and child.name in {"__getattr__", "__dir__"}:
                    # Compatibility module lookup hooks own no codec work.
                    continue
                if any(ast.unparse(d) == "overload" for d in child.decorator_list):
                    continue
                line = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                yield line, prefix + child.name
                yield from walk(child, prefix + child.name + ".")
            else:
                yield from walk(child, prefix)

    yield from walk(ast.parse(path.read_text()))


def _worker(source: Path, rounds: int, fallback: bool) -> dict[str, Any]:
    sys.path.insert(0, str(source))
    from unstd.serde import b64, jsonx

    package = Path(jsonx.__file__).resolve().parent
    if package != source / "unstd" / "serde":
        msg = f"requested {source}, imported {package}"
        raise RuntimeError(msg)
    if fallback:
        b64._HAVE_PYBASE64 = False
        jsonx._HAVE_ORJSON = False
        jsonx._HAVE_MSGSPEC = False
    cases = _workloads()
    for case in cases:
        actual = case.call()
        if not _same(actual, case.expected):
            msg = f"{case.name}: {actual!r} != {case.expected!r}"
            raise AssertionError(msg)
    timings = {}
    for case in cases:
        measurement = measure(case.call, rounds=rounds)
        timings[case.name] = {
            "ns": measurement.per_op_ns,
            "spread": measurement.spread,
            "iters": measurement.iters,
            "rounds": measurement.rounds,
        }
    try:
        from unstd.serde.encoding import _encoder
    except ModuleNotFoundError:
        # Saved comparison revisions retain the earlier private layouts.
        try:
            from unstd.serde._internal.json_encode import _encoder as _grouped_encoder

            _encoder = _grouped_encoder
        except ModuleNotFoundError:
            from unstd.serde._json_encode import _encoder as _legacy_encoder

            _encoder = _legacy_encoder

    _encoder.cache_clear()
    profile = cProfile.Profile()
    profile.enable()
    for _ in range(10):
        for case in cases:
            case.call()
    profile.disable()
    profile_stats: Any = pstats.Stats(profile)
    stats = profile_stats.stats
    functions = []
    for path in sorted(package.rglob("*.py")):
        for line, name in _functions(path):
            entry = stats.get((str(path), line, name.rsplit(".", 1)[-1]))
            functions.append(
                {
                    "function": f"{path.relative_to(package).with_suffix('').as_posix().replace('/', '.')}.{name}",
                    "line": line,
                    "calls": entry[1] if entry else 0,
                    "self_ns": entry[2] * 1e9 if entry else 0,
                    "cumulative_ns": entry[3] * 1e9 if entry else 0,
                }
            )
    from unstd.serde import ndjson, structs

    signatures = {}
    for module in (b64, jsonx, ndjson, structs):
        for name in module.__all__:
            value = getattr(module, name)
            if inspect.isfunction(value) or value is structs.Codec:
                signatures[f"{module.__name__}.{name}"] = str(inspect.signature(value))
    return {
        "source": str(source),
        "fallback": fallback,
        "python": sys.version,
        "platform": platform.platform(),
        "backends": {name: version(name) for name in ("orjson", "msgspec", "pybase64")},
        "timings": timings,
        "functions": functions,
        "signatures": signatures,
    }


def _isolated_cases(source: Path, fallback: bool) -> list[Workload]:
    """Bind a source's modules before restoring the caller's import namespace."""
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "unstd" or name.startswith("unstd.")
    }
    original_path = sys.path[:]
    try:
        for name in saved:
            del sys.modules[name]
        sys.path.insert(0, str(source))
        importlib.invalidate_caches()
        from unstd.serde import b64, jsonx

        package = Path(jsonx.__file__).resolve().parent
        if package != source / "unstd" / "serde":
            msg = f"requested {source}, imported {package}"
            raise RuntimeError(msg)
        if fallback:
            b64._HAVE_PYBASE64 = False
            jsonx._HAVE_ORJSON = False
            jsonx._HAVE_MSGSPEC = False
        return _workloads()
    finally:
        for name in list(sys.modules):
            if name == "unstd" or name.startswith("unstd."):
                del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = original_path


def _paired(
    before: Path, after: Path, rounds: int, repetitions: int, fallback: bool
) -> dict[str, Any]:
    """Alternate versions per workload to reduce long-run clock/scheduler drift."""
    original = _isolated_cases(before, fallback)
    current = _isolated_cases(after, fallback)
    results: dict[str, Any] = {}
    for index, (old, new) in enumerate(zip(original, current, strict=True), 1):
        if old.name != new.name:
            msg = "comparison workload names differ"
            raise AssertionError(msg)
        for case in (old, new):
            if not _same(case.call(), case.expected):
                msg = f"incorrect output in {case.name}"
                raise AssertionError(msg)
        samples: dict[str, list[dict[str, Any]]] = {"before": [], "after": []}
        for repetition in range(repetitions):
            order = (("before", old), ("after", new))
            for label, case in order[:: 1 if repetition % 2 == 0 else -1]:
                result = measure(case.call, rounds=rounds)
                samples[label].append({"ns": result.per_op_ns, "spread": result.spread})
        results[old.name] = samples
        if index % 20 == 0:
            print(f"Measured {index}/{len(original)} paired workloads", file=sys.stderr)
    return {
        "before_source": str(before),
        "after_source": str(after),
        "fallback": fallback,
        "rounds": rounds,
        "repetitions": repetitions,
        "python": sys.version,
        "platform": platform.platform(),
        "backends": {name: version(name) for name in ("orjson", "msgspec", "pybase64")},
        "workloads": results,
    }


def main() -> None:
    """Run isolated workers and save their timing and function coverage evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[2] / "src"
    )
    parser.add_argument("--compare-source", type=Path)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument(
        "--paired",
        action="store_true",
        help="alternate sources per workload in one interpreter",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.rounds < 1 or args.repetitions < 1:
        parser.error("rounds and repetitions must be positive")
    if args.worker:
        print(json.dumps(_worker(args.source.resolve(), args.rounds, args.fallback)))
        return
    if args.paired:
        if args.compare_source is None:
            parser.error("--paired requires --compare-source")
        paired = _paired(
            args.compare_source.resolve(),
            args.source.resolve(),
            args.rounds,
            args.repetitions,
            args.fallback,
        )
        rendered = json.dumps(paired, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered)
        else:
            print(rendered)
        print(f"Measured {len(paired['workloads'])} paired workloads", file=sys.stderr)
        return
    sources = [args.source.resolve()]
    if args.compare_source:
        sources.insert(0, args.compare_source.resolve())
    runs: list[dict[str, Any]] = []
    bootstrap = (
        "import runpy, sys; "
        "sys.path.insert(0, sys.argv.pop(1)); "
        "runpy.run_module('bench.serde_profile', run_name='__main__')"
    )
    for repetition in range(args.repetitions):
        for source in sources[:: 1 if repetition % 2 == 0 else -1]:
            command = [
                sys.executable,
                "-c",
                bootstrap,
                str(source),
                "--worker",
                "--source",
                str(source),
                "--rounds",
                str(args.rounds),
            ]
            if args.fallback:
                command.append("--fallback")
            result = subprocess.run(  # noqa: S603 - fixed local benchmark command
                command, check=False, capture_output=True, text=True
            )
            if result.returncode:
                msg = f"benchmark worker for {source} failed:\n{result.stderr}"
                raise RuntimeError(msg)
            run = json.loads(result.stdout)
            if runs and run["signatures"] != runs[0]["signatures"]:
                msg = "public signatures differ between benchmark workers"
                raise AssertionError(msg)
            runs.append(run)
            print(
                f"Measured {source}: {len(run['timings'])} workloads", file=sys.stderr
            )
    output = {"runs": runs}
    if args.output:
        args.output.write_text(json.dumps(output, indent=2) + "\n")
    else:
        print(json.dumps(output, indent=2))
    for run in runs[-len(sources) :]:
        missing = [f["function"] for f in run["functions"] if not f["calls"]]
        print(
            f"{run['source']}: {len(run['functions']) - len(missing)}/{len(run['functions'])} Python functions exercised; uncovered: {missing}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
