"""Finite cache and OS-resource probes, called only in owned resource workers."""

from __future__ import annotations

import copy
import dataclasses
import gc
import os
from pathlib import Path
import tempfile
import weakref

from unstd import fs, rex
from unstd.clone import structural


def rex_cache(*, size: int = 1024) -> dict:
    """Exercise real accepted and declined patterns past the published capacity."""
    assert size > 512, "cache probe must cross the documented capacity"
    rex.purge()
    held = rex.compile(r"^held-resource$")
    declined = 0
    try:
        for index in range(size):
            word = f"resource-{index}"
            pattern = rf"^({word})\1$" if index % 2 else rf"^{word}$"
            subject = word * 2 if index % 2 else word
            compiled = rex.compile(pattern)
            assert compiled.fullmatch(subject) is not None
            assert compiled.fullmatch(subject + "!") is None
            declined += index % 2
        entries = len(rex._cache)
        assert entries == 512, f"regex cache retained {entries} entries"
        assert (str, r"^held-resource$", 0) not in rex._cache
        assert held.fullmatch("held-resource") is not None
        rex.purge()
        assert len(rex._cache) == 0
        assert held.fullmatch("held-resource") is not None
        return {
            "patterns": size,
            "declined_patterns": declined,
            "entries": entries,
            "after_purge": 0,
        }
    finally:
        rex.purge()


def _class_batch(operation, size: int) -> list[weakref.ReferenceType]:
    references = []
    for index in range(size):
        record = dataclasses.make_dataclass(f"ResourceRecord{index}", [("items", list)])
        source = record([index, {"nested": [index + 1]}])
        result = operation(source)
        items = result["items"] if isinstance(result, dict) else result.items
        assert items == [index, {"nested": [index + 1]}]
        items[1]["nested"].append(-1)
        assert source.items == [index, {"nested": [index + 1]}]
        references.append(weakref.ref(record))
    return references


def clone_cache(*, size: int = 128) -> dict:
    """Observe collection after class churn, with stdlib collection controls."""
    assert size > 0
    results = {}
    for name, operation, reference in (
        ("deep", structural.deep, copy.deepcopy),
        ("asdict", structural.asdict, dataclasses.asdict),
    ):
        control = _class_batch(reference, size)
        observed = _class_batch(operation, size)
        gc.collect()
        control_alive = sum(item() is not None for item in control)
        retained = sum(item() is not None for item in observed)
        assert control_alive == 0, "stdlib collection control failed"
        assert retained == 0, f"{name} retained {retained} generated classes"
        results[name] = {
            "classes": size,
            "control_alive": control_alive,
            "retained": retained,
        }
    return results


def _descriptor_count() -> tuple[int, str]:
    for directory in ("/proc/self/fd", "/dev/fd"):
        if Path(directory).is_dir():
            with os.scandir(directory) as entries:
                return sum(entry.name.isdecimal() for entry in entries), directory
    message = "descriptor probes require /proc/self/fd or /dev/fd"
    raise RuntimeError(message)


def filesystem(*, size: int = 32) -> dict:
    """Repeat real writes, body rollback and early traversal closure without growth."""
    assert size > 0
    gc.collect()
    before, source = _descriptor_count()
    failures = 0
    with tempfile.TemporaryDirectory(prefix="unstd-resources-") as scratch:
        root = Path(scratch)
        dest = root / "state"
        for index in range(size):
            expected = f"generation {index}\n".encode()
            assert fs.atomic_write(dest, expected) == dest
            assert dest.read_bytes() == expected
            failure = OSError("resource rollback control")
            observed = None
            try:
                with fs.atomic_writer(dest, mode="wb") as stream:
                    stream.write(b"partial replacement")
                    raise failure
            except OSError as caught:
                observed = caught
            assert observed is failure, "writer did not preserve the body failure"
            failures += 1
            assert dest.read_bytes() == expected
            assert list(root.iterdir()) == [dest], "atomic writer left scratch files"
        nested = root / "one" / "two" / "three"
        nested.mkdir(parents=True)
        leaf = nested / "leaf"
        leaf.write_bytes(b"permitted")
        for _ in range(size):
            walk = fs.walk_files(root / "one")
            assert next(walk) == leaf
            walk.close()
        assert set(fs.walk_files(root)) == {dest, leaf}
        artifacts = sorted(
            path.relative_to(root).as_posix() for path in root.rglob("*")
        )
        assert artifacts == [
            "one",
            "one/two",
            "one/two/three",
            "one/two/three/leaf",
            "state",
        ]
    gc.collect()
    after, after_source = _descriptor_count()
    assert source == after_source
    assert after == before, f"descriptor growth: {before} -> {after} ({source})"
    assert failures == size
    return {
        "writes": size,
        "rollbacks": failures,
        "closed_walks": size,
        "descriptors_before": before,
        "descriptors_after": after,
        "descriptor_source": source,
        "scratch_files": 0,
    }
