"""Versioned deterministic byte mutations, independent of Python hash seeding."""

from __future__ import annotations

import hashlib
import random
from typing import TYPE_CHECKING

from tests.tooling.fuzz.registry import Seed


if TYPE_CHECKING:
    from collections.abc import Iterator


VERSION = 1
OPERATORS = (
    "flip",
    "insert",
    "delete",
    "duplicate",
    "truncate",
    "splice",
    "selector",
    "boundary",
    "arithmetic",
)


def mutations(
    seeds: tuple[Seed, ...], *, runs: int, seed: int, max_input_bytes: int = 65536
) -> Iterator[tuple[str, bytes]]:
    """Yield a reproducible campaign with an explicit count and allocation bound."""
    if (
        not isinstance(seeds, tuple)
        or not seeds
        or type(runs) is not int
        or not 1 <= runs <= 100000
    ):
        msg = "mutation requires a corpus and 1..100000 runs"
        raise ValueError(msg)
    if type(seed) is not int or not 0 <= seed < 2**64:
        msg = "campaign seed must be an unsigned 64-bit integer"
        raise ValueError(msg)
    if type(max_input_bytes) is not int or not 1 <= max_input_bytes <= 65536:
        msg = "mutation input budget must be within 1..65536"
        raise ValueError(msg)
    if any(
        not isinstance(item, Seed) or type(item.data) is not bytes for item in seeds
    ):
        msg = "mutation corpus requires immutable Seed byte records"
        raise ValueError(msg)
    ordered = sorted(seeds, key=lambda item: item.identity)
    if len({item.identity for item in ordered}) != len(ordered):
        msg = "mutation corpus identities must be unique"
        raise ValueError(msg)
    for index in range(runs):
        entropy = hashlib.sha256(
            f"unstd-mutator-v{VERSION}:{seed}:{index}".encode()
        ).digest()
        rng = random.Random(int.from_bytes(entropy, "big"))  # noqa: S311 — reproducible input mutation
        source = ordered[rng.randrange(len(ordered))]
        value = bytearray(source.data[:max_input_bytes])
        operation = index % len(OPERATORS)
        position = rng.randrange(len(value) + 1)
        if operation == 0:
            if value:
                value[min(position, len(value) - 1)] ^= 1 << rng.randrange(8)
            else:
                value.append(rng.randrange(256))
        elif operation == 1:
            chunk = rng.randbytes(rng.randrange(1, min(max_input_bytes, 32) + 1))
            value[position:position] = chunk
        elif operation == 2:
            del value[position : position + rng.randrange(1, 33)]
        elif operation == 3:
            value[position:position] = value[max(0, position - 32) : position] or b"\0"
        elif operation == 4:
            del value[position:]
        elif operation == 5:
            other = ordered[rng.randrange(len(ordered))].data
            value[position:] = other[: max_input_bytes - position]
        elif operation == 6:
            value[:1] = bytes([rng.randrange(256)])
        elif operation == 7:
            fragments = (
                b"\0",
                b"\xff",
                b"\xed\xa0\x80",
                b"\r\n",
                b"=",
                b"..",
                b"1e999",
                b"NaN",
                b"[]{}",
                b"\xff" * 8,
            )
            value[position:position] = fragments[rng.randrange(len(fragments))]
        elif value:
            offset = min(position, len(value) - 1)
            value[offset] = (value[offset] + rng.choice((-128, -1, 1, 127))) % 256
        yield (
            f"mutation-v{VERSION}/{seed}/{index}/{OPERATORS[operation]}/{source.identity}",
            bytes(value[:max_input_bytes]),
        )
