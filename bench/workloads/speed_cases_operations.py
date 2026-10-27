"""Checked workloads for cloning, text, regex, iterators, files, processes and caches."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import inspect
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from bench.speeds import Case, expect


if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import ExitStack

    from unstd.serde.jsonx import Json


EXCLUSIONS = {
    "unstd.rex.Match": "Re-exported match-result vocabulary; produced by matching workloads.",
    "unstd.rex.Pattern": "Re-exported pattern vocabulary; produced by compilation workloads.",
    "unstd.rex.RegexFlag": "Re-exported enum vocabulary, not a regex operation.",
}


@dataclass
class _Record:
    name: str
    values: list[int]


def _near(expected: float) -> Callable[[object], None]:
    """Check independently calculated similarity scores without float-bit coupling."""

    def check(value: object) -> None:
        if not isinstance(value, float) or not math.isclose(
            value, expected, rel_tol=1e-12, abs_tol=1e-12
        ):
            msg = f"expected similarity {expected!r}, got {value!r}"
            raise AssertionError(msg)

    return check


def _clone_cases() -> list[Case]:
    from unstd import clone

    source = {"name": "record", "values": list(range(64)), "meta": {"active": True}}
    expected = {"name": "record", "values": list(range(64)), "meta": {"active": True}}
    record = _Record("record", list(range(64)))

    def deep_check(value: object) -> None:
        expect(expected)(value)
        if (
            value is source
            or value["values"] is source["values"]  # type: ignore[index]
            or value["meta"] is source["meta"]  # type: ignore[index]
        ):
            msg = "deep clone retained a mutable source reference"
            raise AssertionError(msg)

    def shallow_check(value: object) -> None:
        expect(expected)(value)
        if value is source or value["values"] is not source["values"]:  # type: ignore[index]
            msg = "shallow copy did not retain exactly the nested values"
            raise AssertionError(msg)

    return [
        Case(
            "unstd.clone.copy",
            partial(clone.copy, source),
            shallow_check,
            "Shallow copy of a three-key dictionary containing a 64-item list.",
        ),
        Case(
            "unstd.clone.deepcopy",
            partial(clone.deepcopy, source),
            deep_check,
            "Stdlib deep copy alias; three-key dictionary and 64-item list.",
        ),
        Case(
            "unstd.clone.deep",
            partial(clone.deep, source),
            deep_check,
            "Structural deep copy; three-key dictionary and 64-item list.",
        ),
        Case(
            "unstd.clone.asdict",
            partial(clone.asdict, record),
            expect({"name": "record", "values": list(range(64))}),
            "Dataclass with one string and a 64-item integer list.",
        ),
    ]


def _text_cases(*, comparison: bool = False) -> list[Case]:
    from unstd.text import fuzz

    names = ("cat", "dog", "bat", "x") * 8
    queries = ("cat", "dog", "bat") * 4
    expected_rows = {
        "cat": (1.0, 0.0, 2 / 3, 0.0),
        "dog": (0.0, 1.0, 0.0, 0.0),
        "bat": (2 / 3, 0.0, 1.0, 0.0),
    }
    matrix = [list(expected_rows[query] * 8) for query in queries]

    def matrix_check(value: object) -> None:
        if not isinstance(value, list) or len(value) != len(matrix):
            msg = "similarity matrix has the wrong row count"
            raise AssertionError(msg)
        for actual, expected in zip(value, matrix, strict=True):
            if not isinstance(actual, list) or len(actual) != len(expected):
                msg = "similarity matrix has the wrong column count"
                raise AssertionError(msg)
            for score, target in zip(actual, expected, strict=True):
                _near(target)(score)

    return [
        Case(
            "unstd.text.fuzz.ratio",
            partial(fuzz.ratio, "kitten", "sitting"),
            _near(8 / 13),
            "Pairwise similarity for kitten/sitting; both selected metrics give 8/13.",
        ),
        *(
            [
                Case(
                    "unstd.text.fuzz.indel_ratio",
                    partial(fuzz.indel_ratio, "kitten", "sitting"),
                    _near(8 / 13),
                    "Indel similarity for kitten/sitting; LCS has four characters.",
                )
            ]
            if not comparison or hasattr(fuzz, "indel_ratio")
            else []
        ),
        *(
            [
                Case(
                    "unstd.text.fuzz.difflib_ratio",
                    partial(fuzz.difflib_ratio, "kitten", "sitting"),
                    _near(8 / 13),
                    "SequenceMatcher similarity for kitten/sitting.",
                )
            ]
            if not comparison or hasattr(fuzz, "difflib_ratio")
            else []
        ),
        Case(
            "unstd.text.fuzz.distance",
            partial(fuzz.distance, "kitten", "sitting"),
            expect(3),
            "Levenshtein distance for kitten/sitting; three edits.",
        ),
        Case(
            "unstd.text.fuzz.jaro_winkler",
            partial(fuzz.jaro_winkler, "MARTHA", "MARHTA"),
            _near(0.9611111111111111),
            "Classic MARTHA/MARHTA transposition and three-character prefix.",
        ),
        Case(
            "unstd.text.fuzz.best_match",
            partial(fuzz.best_match, "cat", names),
            expect(("cat", 1.0)),
            "Best of 32 short names, with an exact match and stable ties.",
        ),
        Case(
            "unstd.text.fuzz.extract",
            partial(fuzz.extract, "cat", names, limit=5),
            expect([("cat", 1.0)] * 5),
            "Top five of 32 short names, with eight exact ties.",
        ),
        Case(
            "unstd.text.fuzz.cdist",
            partial(fuzz.cdist, queries, names),
            matrix_check,
            "Fully materialized 12-by-32 similarity matrix; one worker.",
        ),
    ]


def _iterator_cases() -> list[Case]:
    from unstd import iters

    values = tuple(range(64))
    chunks = [values[start : start + 8] for start in range(0, 64, 8)]
    windows = [values[start : start + 4] for start in range(61)]
    duplicated = tuple(i // 4 for i in range(64))

    def partition() -> tuple[list[int], list[int]]:
        left, right = iters.partition(values, lambda value: value % 2)
        return list(left), list(right)

    return [
        Case(
            "unstd.iters.batched",
            lambda: list(iters.batched(values, 8)),
            expect(chunks),
            "Fully consume eight stdlib batches from a reusable 64-item tuple.",
        ),
        Case(
            "unstd.iters.chunked",
            lambda: list(iters.chunked(values, 8)),
            expect(chunks),
            "Fully consume eight chunks from a reusable 64-item tuple.",
        ),
        Case(
            "unstd.iters.first",
            partial(iters.first, values),
            expect(0),
            "First value from a 64-item tuple.",
        ),
        Case(
            "unstd.iters.flatten",
            lambda: list(iters.flatten(chunks)),
            expect(list(values)),
            "Fully flatten eight eight-item tuples.",
        ),
        Case(
            "unstd.iters.ilen",
            partial(iters.ilen, values),
            expect(64),
            "Count a reusable 64-item tuple.",
        ),
        Case(
            "unstd.iters.last",
            partial(iters.last, values),
            expect(63),
            "Last value from a reusable 64-item tuple.",
        ),
        Case(
            "unstd.iters.nth",
            partial(iters.nth, values, 31),
            expect(31),
            "Select index 31 from a reusable 64-item tuple.",
        ),
        Case(
            "unstd.iters.pairwise",
            lambda: list(iters.pairwise(values)),
            expect([(index, index + 1) for index in range(63)]),
            "Fully consume 63 adjacent pairs.",
        ),
        Case(
            "unstd.iters.partition",
            partition,
            expect((list(range(0, 64, 2)), list(range(1, 64, 2)))),
            "Fully consume both halves of a 64-item even/odd partition.",
        ),
        Case(
            "unstd.iters.runs",
            lambda: list(iters.runs(duplicated, lambda value: value)),
            expect([(i, (i,) * 4) for i in range(16)]),
            "Fully consume sixteen adjacent four-item runs.",
        ),
        Case(
            "unstd.iters.take",
            partial(iters.take, values, 16),
            expect(list(range(16))),
            "Materialize the first sixteen values from a 64-item tuple.",
        ),
        Case(
            "unstd.iters.unique_everseen",
            lambda: list(iters.unique_everseen(duplicated)),
            expect(list(range(16))),
            "Fully consume 64 hashable values containing sixteen distinct integers.",
        ),
        Case(
            "unstd.iters.unique_justseen",
            lambda: list(iters.unique_justseen(duplicated)),
            expect(list(range(16))),
            "Fully consume sixteen adjacent duplicate groups.",
        ),
        Case(
            "unstd.iters.windowed",
            lambda: list(iters.windowed(values, 4)),
            expect(windows),
            "Fully consume 61 overlapping four-item windows.",
        ),
        Case(
            "unstd.iters.windowed#strided",
            lambda: list(iters.windowed(values, 4, step=3)),
            expect([values[start : start + 4] for start in range(0, 61, 3)]),
            "Fully consume four-item windows at stride three.",
        ),
    ]


def _regex_cases(stack: ExitStack, *, comparison: bool = False) -> list[Case]:
    from unstd import rex

    # Restore private front caches so these fixtures do not change later tests.
    for name in ("_cache", "_faithful_cache", "_linear_cache"):
        if comparison and not hasattr(rex, name):
            continue
        cache = getattr(rex, name)
        stack.callback(_restore_cache, cache, cache.copy())

    pattern = r"[a-z]+"
    text = "alpha 123 beta 456 gamma"

    def compiled_check(value: Any) -> None:
        match_check(value.search(text))
        match_check(value.fullmatch("alpha"))
        expect(None)(value.fullmatch("123"))

    def match_check(value: Any) -> None:
        if value is None:
            msg = "expected a regex match"
            raise AssertionError(msg)
        expect("alpha")(value.group())
        expect((0, 5))(value.span())

    def iterator_check(value: Any) -> None:
        expect(["alpha", "beta", "gamma"])([match.group() for match in value])

    return [
        Case(
            "unstd.rex.compile",
            partial(rex.compile, pattern),
            compiled_check,
            "Warm-cache compilation of an ASCII character-class pattern.",
        ),
        *(
            [
                Case(
                    "unstd.rex.compile_faithful",
                    partial(rex.compile_faithful, pattern),
                    compiled_check,
                    "Warm-cache faithful compilation of an ASCII character-class pattern.",
                )
            ]
            if not comparison or hasattr(rex, "compile_faithful")
            else []
        ),
        *(
            [
                Case(
                    "unstd.rex.compile_linear",
                    partial(rex.compile_linear, pattern),
                    compiled_check,
                    "Warm-cache strict linear compilation of an ASCII character-class pattern.",
                )
            ]
            if not comparison or hasattr(rex, "compile_linear")
            else []
        ),
        Case(
            "unstd.rex.escape",
            partial(rex.escape, "hello.[world]+?"),
            expect(r"hello\.\[world\]\+\?"),
            "Escape a fourteen-character string containing regex punctuation.",
        ),
        Case(
            "unstd.rex.findall",
            partial(rex.findall, pattern, text),
            expect(["alpha", "beta", "gamma"]),
            "Materialize three word matches in 24 characters.",
        ),
        Case(
            "unstd.rex.finditer",
            lambda: list(rex.finditer(pattern, text)),
            iterator_check,
            "Fully consume three match objects in 24 characters.",
        ),
        Case(
            "unstd.rex.fullmatch",
            partial(rex.fullmatch, pattern, "alpha"),
            match_check,
            "Match a complete five-character word.",
        ),
        Case(
            "unstd.rex.match",
            partial(rex.match, pattern, text),
            match_check,
            "Match a five-character word at the start of 24 characters.",
        ),
        Case(
            "unstd.rex.search",
            partial(rex.search, pattern, text),
            match_check,
            "Search for the first five-character word in 24 characters.",
        ),
        Case(
            "unstd.rex.split",
            partial(rex.split, r"\s+", text),
            expect(["alpha", "123", "beta", "456", "gamma"]),
            "Split 24 characters into five whitespace-separated fields.",
        ),
        Case(
            "unstd.rex.sub",
            partial(rex.sub, pattern, "X", text),
            expect("X 123 X 456 X"),
            "Replace three word matches in 24 characters.",
        ),
        Case(
            "unstd.rex.subn",
            partial(rex.subn, pattern, "X", text),
            expect(("X 123 X 456 X", 3)),
            "Replace and count three word matches in 24 characters.",
        ),
        Case(
            "unstd.rex.purge",
            rex.purge,
            expect(None),
            "Purge all regex caches; repeated calls measure the empty-cache path.",
        ),
    ]


def _restore_cache(cache: dict[Any, Any], saved: dict[Any, Any]) -> None:
    cache.clear()
    cache.update(saved)


def _filesystem_cases(stack: ExitStack) -> list[Case]:
    from unstd import fs

    root = Path(stack.enter_context(TemporaryDirectory(prefix="unstd-speeds-")))
    existing = root / "existing"
    existing.mkdir()
    tree = root / "walk"
    tree.mkdir()
    paths = []
    for index in range(4):
        directory = tree / str(index)
        directory.mkdir()
        for number in range(8):
            path = directory / f"{number}.txt"
            path.write_bytes(b"fixture\n")
            paths.append(path)
    payload = b"payload\n" * 128
    destination = root / "written.bin"
    writer_path = root / "writer.bin"
    first = root / "replace-a.bin"
    second = root / "replace-b.bin"
    first.write_bytes(payload)
    replacement_paths = [first, second]

    def atomic_writer() -> None:
        with fs.atomic_writer(writer_path, mode="wb") as stream:
            stream.write(payload)

    def check_writer(value: object) -> None:
        expect(None)(value)
        expect(payload)(writer_path.read_bytes())

    def check_write(value: object) -> None:
        expect(destination)(value)
        expect(payload)(destination.read_bytes())

    def replace() -> Path:
        source, target = replacement_paths
        result = fs.atomic_replace(source, target)
        replacement_paths.reverse()
        return result

    def check_replace(value: Any) -> None:
        expect(payload)(value.read_bytes())
        if replacement_paths[1].exists():
            msg = "atomic replacement left the old source behind"
            raise AssertionError(msg)

    return [
        Case(
            "unstd.fs.ensure_dir",
            partial(fs.ensure_dir, existing),
            expect(existing),
            "Idempotent directory creation for an existing directory in a private temporary tree.",
        ),
        Case(
            "unstd.fs.sync_dir",
            partial(fs.sync_dir, existing),
            expect(None),
            "Best-effort directory fsync of an existing private directory; real filesystem I/O.",
        ),
        Case(
            "unstd.fs.atomic_write",
            partial(fs.atomic_write, destination, payload),
            check_write,
            "Write 1 KiB through a scratch file, fsync, and rename; no parent fsync.",
        ),
        Case(
            "unstd.fs.atomic_writer",
            atomic_writer,
            check_writer,
            "Enter, write 1 KiB, flush, fsync, and close the atomic writer context.",
        ),
        Case(
            "unstd.fs.atomic_replace",
            replace,
            check_replace,
            "Alternate atomic renames of one 1 KiB file between two private paths; no payload rewrite.",
        ),
        Case(
            "unstd.fs.walk_files",
            lambda: sorted(fs.walk_files(tree)),
            expect(sorted(paths)),
            "Fully walk four directories containing 32 files, without following links; sort results.",
        ),
    ]


def _process_cases() -> list[Case]:
    from unstd import proc

    argv = (sys.executable, "-I", "-S", "-c", "print('speed-fixture')")
    completed = proc.CompletedProcess(argv, 0, "speed-fixture\n", "", 0.0)

    def check_run(value: Any) -> None:
        if type(value) is not proc.CompletedProcess:
            msg = "process run returned the wrong result type"
            raise AssertionError(msg)
        expect(argv)(value.argv)
        expect(0)(value.returncode)
        expect("speed-fixture\n")(value.stdout)
        expect("")(value.stderr)
        if not math.isfinite(value.duration) or value.duration < 0:
            msg = "process duration must be finite and nonnegative"
            raise AssertionError(msg)

    return [
        Case(
            "unstd.proc.CompletedProcess",
            partial(proc.CompletedProcess, argv, 0, "speed-fixture\n", "", 0.0),
            expect(completed),
            "Construct an immutable five-field captured process result.",
        ),
        Case(
            "unstd.proc.CompletedProcess.ok",
            lambda: completed.ok,
            expect(True),
            "Read the success property of a prebuilt zero-exit process result.",
        ),
        Case(
            "unstd.proc.run",
            partial(proc.run, argv, timeout=5.0),
            check_run,
            "Spawn isolated Python without site imports, print one line, capture and reap the real child.",
        ),
        Case(
            "unstd.proc.run_ok",
            partial(proc.run_ok, argv, timeout=5.0),
            expect(True),
            "Spawn, capture and reap a real isolated Python child, then test its zero exit.",
        ),
        Case(
            "unstd.proc.capture",
            partial(proc.capture, argv, timeout=5.0),
            expect("speed-fixture"),
            "Spawn, capture and reap a real isolated Python child, then strip its one output line.",
        ),
    ]


def _cache_cases(stack: ExitStack, *, comparison: bool = False) -> list[Case]:
    try:
        from unstd.cache import Cache
    except ImportError:
        if comparison:
            return []
        raise

    directory = Path(
        stack.enter_context(TemporaryDirectory(prefix="unstd-cache-speed-"))
    )
    path = directory / "results.sqlite3"
    reader = Cache(path, namespace="read")
    writer = Cache(path, namespace="write")
    remover = Cache(path, namespace="delete")
    clearer = Cache(path, namespace="clear")
    computer = Cache(path, namespace="compute")
    payload: Json = {"name": "result", "values": list(range(32)), "active": True}
    expected = {"name": "result", "values": list(range(32)), "active": True}
    reader.set("existing", payload)
    computer.set("warm", payload)

    def check_constructed(value: Any) -> None:
        if type(value) is not Cache:
            msg = "cache constructor returned an unexpected type"
            raise AssertionError(msg)
        expect("absent")(value.get("missing", "absent"))

    def check_set(value: object) -> None:
        expect(None)(value)
        expect(expected)(writer.get("result"))

    def delete_existing() -> bool:
        remover.set("result", payload)
        return remover.delete("result")

    def check_deleted(value: object) -> None:
        expect(True)(value)
        expect("absent")(remover.get("result", "absent"))

    def clear_entries() -> int:
        clearer.set("first", payload)
        clearer.set("second", payload)
        return clearer.clear()

    def check_cleared(value: object) -> None:
        expect(2)(value)
        expect("absent")(clearer.get("first", "absent"))
        expect("absent")(clearer.get("second", "absent"))
        expect(expected)(reader.get("existing"))

    def unexpected_factory() -> Json:
        msg = "a warm cache entry invoked its producer"
        raise AssertionError(msg)

    def cold_compute() -> Json:
        computer.delete("cold")
        return computer.get_or_compute("cold", lambda: payload)

    codec_cases: list[Case] = []
    if not comparison or "codec" in inspect.signature(Cache.get_or_compute).parameters:

        class IntegerCodec:
            """One signed 64-bit artifact with an exact-size decode contract."""

            def encode(self, value: int, /) -> bytes:
                return value.to_bytes(8, "little", signed=True)

            def decode(self, raw: bytes, /) -> int:
                if len(raw) != 8:
                    msg = "integer artifact must contain exactly eight bytes"
                    raise ValueError(msg)
                return int.from_bytes(raw, "little", signed=True)

        # Keep new artifact setup out of the existing JSON workload database.
        codec_computer = Cache(
            directory / "codec-results.sqlite3", namespace="codec-compute-v1"
        )
        codec = IntegerCodec()
        number = -(1 << 40) - 17
        codec_computer.get_or_compute("warm", lambda: number, codec=codec)

        def unexpected_integer() -> int:
            msg = "a warm typed artifact invoked its producer"
            raise AssertionError(msg)

        def cold_integer() -> int:
            codec_computer.delete("cold")
            return codec_computer.get_or_compute("cold", lambda: number, codec=codec)

        codec_cases = [
            Case(
                "unstd.cache.Cache.get_or_compute#codec-hit",
                partial(
                    codec_computer.get_or_compute,
                    "warm",
                    unexpected_integer,
                    codec=codec,
                ),
                expect(number),
                "Read and validate one eight-byte signed 64-bit artifact through a structural codec; a warm producer must remain uncalled.",
            ),
            Case(
                "unstd.cache.Cache.get_or_compute#codec-miss",
                cold_integer,
                expect(number),
                "Delete a key, produce one signed 64-bit value, encode and validate its eight-byte artifact, then commit; includes deletion.",
            ),
        ]

    batch_cases: list[Case] = []
    if not comparison or hasattr(Cache, "get_many"):
        batch_reader = Cache(path, namespace="batch-read")
        batch_writer = Cache(path, namespace="batch-write")
        batch_values = {f"summary-{index}": payload for index in range(64)}
        batch_expected = dict.fromkeys(batch_values, expected)
        batch_reader.set_many(batch_values)

        def check_batch_set(value: object) -> None:
            expect(None)(value)
            expect(batch_expected)(batch_writer.get_many(batch_values))

        batch_cases = [
            Case(
                "unstd.cache.Cache.get_many",
                partial(batch_reader.get_many, batch_values),
                expect(batch_expected),
                "Read and decode 64 persisted 32-integer JSON records in one consistent SQLite snapshot.",
            ),
            Case(
                "unstd.cache.Cache.set_many",
                partial(batch_writer.set_many, batch_values),
                check_batch_set,
                "Encode and atomically replace 64 32-integer JSON records in one committed WAL transaction.",
            ),
        ]

    return [
        Case(
            "unstd.cache.Cache",
            partial(Cache, path, namespace="construct"),
            check_constructed,
            "Open and validate an existing local SQLite WAL cache file in an empty namespace.",
        ),
        Case(
            "unstd.cache.Cache.get#hit",
            partial(reader.get, "existing"),
            expect(expected),
            "Read and decode one persisted JSON record containing 32 integers through a fresh connection.",
        ),
        Case(
            "unstd.cache.Cache.get#miss",
            partial(reader.get, "missing", "absent"),
            expect("absent"),
            "Look up an absent key in a populated local WAL cache and return the caller default.",
        ),
        Case(
            "unstd.cache.Cache.set",
            partial(writer.set, "result", payload),
            check_set,
            "Encode and replace a 32-integer JSON record in a committed local WAL transaction.",
        ),
        Case(
            "unstd.cache.Cache.delete",
            delete_existing,
            check_deleted,
            "Set then delete one existing 32-integer JSON record; includes committed setup and deletion.",
        ),
        Case(
            "unstd.cache.Cache.clear",
            clear_entries,
            check_cleared,
            "Set two JSON records then clear their namespace; includes setup and checks isolation outside timing.",
        ),
        Case(
            "unstd.cache.Cache.get_or_compute#hit",
            partial(computer.get_or_compute, "warm", unexpected_factory),
            expect(expected),
            "Read a warm 32-integer JSON result and refuse any unexpected producer invocation.",
        ),
        Case(
            "unstd.cache.Cache.get_or_compute#miss",
            cold_compute,
            expect(expected),
            "Delete a key then compute, encode and commit its 32-integer JSON result; includes deletion.",
        ),
        *batch_cases,
        *codec_cases,
    ]


def cases(stack: ExitStack, *, comparison: bool = False) -> list[Case]:
    """Build independent, bounded workloads and register owned fixture cleanup."""
    return [
        *_clone_cases(),
        *_text_cases(comparison=comparison),
        *_iterator_cases(),
        *_regex_cases(stack, comparison=comparison),
        *_filesystem_cases(stack),
        *_process_cases(),
        *_cache_cases(stack, comparison=comparison),
    ]
