"""Versioned deterministic fault ordering and bounded, JSON-only evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib.abc
import json
import os
import random
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


VERSION = 1
MAX_EVENTS = 1000
MAX_JOURNAL_BYTES = 65536
BLOCKED_BACKENDS = (
    "orjson",
    "msgspec",
    "pybase64",
    "uuid_utils",
    "blake3",
    "numpy",
    "soundfile",
    "zlib_ng",
    "tomlkit",
    "ruamel",
    "irgx",
    "whenever",
    "rapidfuzz",
)


class _BlockBackends(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(
            fullname == prefix or fullname.startswith(prefix + ".")
            for prefix in BLOCKED_BACKENDS
        ):
            message = f"stdlib chaos worker blocks {fullname}"
            raise ModuleNotFoundError(message)
        return


def apply_backend_policy() -> None:
    """Apply the campaign's fallback policy before any product backend imports."""
    if os.environ.get("UNSTD_CHAOS_BACKEND") != "stdlib":
        return
    loaded = [
        name
        for name in sys.modules
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in BLOCKED_BACKENDS
        )
    ]
    if loaded:
        message = f"chaos fallback admission happened after backend import: {loaded}"
        raise RuntimeError(message)
    if not any(isinstance(finder, _BlockBackends) for finder in sys.meta_path):
        sys.meta_path.insert(0, _BlockBackends())


def ordered[T](seed: int, namespace: str, values: Sequence[T]) -> tuple[T, ...]:
    """Order a finite reviewed schedule without changing the global RNG state."""
    if type(seed) is not int or not 0 <= seed < 2**64:
        message = "chaos seed must be an unsigned 64-bit integer"
        raise ValueError(message)
    if not isinstance(namespace, str) or not namespace or len(namespace) > 256:
        message = "schedule namespace must contain 1..256 characters"
        raise ValueError(message)
    if not isinstance(values, tuple | list) or not 1 <= len(values) <= 10000:
        message = "schedule requires 1..10000 reviewed values"
        raise ValueError(message)
    entropy = hashlib.sha256(
        f"unstd-chaos-v{VERSION}:{seed}:{namespace}".encode("utf-8", "surrogatepass")
    ).digest()
    rng = random.Random(int.from_bytes(entropy, "big"))  # noqa: S311 - recorded fault ordering
    result = list(values)
    rng.shuffle(result)
    return tuple(result)


@dataclass
class ChaosJournal:
    """A value snapshot per event; evidence cannot grow without a fixed bound."""

    seed: int
    scenario: str
    sink: Callable[[dict], None] | None = field(default=None, repr=False, compare=False)
    _events: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self):
        if type(self.seed) is not int or not 0 <= self.seed < 2**64:
            message = "chaos seed must be an unsigned 64-bit integer"
            raise ValueError(message)
        if not isinstance(self.scenario, str) or not self.scenario:
            message = "chaos scenario identity must be nonempty"
            raise ValueError(message)

    def record(self, event: str, **fields) -> None:
        """Retain an immutable JSON snapshot of a planned or observed boundary."""
        if not isinstance(event, str) or not event or len(event) > 128:
            message = "journal event must contain 1..128 characters"
            raise ValueError(message)
        if "event" in fields:
            message = "event is reserved journal metadata"
            raise ValueError(message)
        row = json.loads(json.dumps({"event": event, **fields}, allow_nan=False))
        candidate = [*self._events, row]
        if (
            len(candidate) > MAX_EVENTS
            or len(json.dumps(candidate).encode()) > MAX_JOURNAL_BYTES
        ):
            message = "chaos journal exceeds its evidence budget"
            raise ValueError(message)
        self._events.append(row)
        if self.sink is not None:
            self.sink(self.snapshot())

    def snapshot(self) -> dict:
        """The exact event history, insulated from caller mutation."""
        return json.loads(
            json.dumps(
                {
                    "version": VERSION,
                    "seed": self.seed,
                    "scenario": self.scenario,
                    "events": self._events,
                }
            )
        )


def journal_sink(directory: Path | None, scenario: str):
    """Publish each completed record atomically inside the worker-owned directory."""
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (hashlib.sha256(scenario.encode()).hexdigest() + ".json")

    def publish(snapshot):
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, allow_nan=False), encoding="utf-8")
        temporary.replace(destination)

    return publish
