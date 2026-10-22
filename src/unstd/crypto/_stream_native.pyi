from collections.abc import Iterable
from types import NotImplementedType

from blake3 import blake3

def absorb(hasher: blake3, chunks: Iterable[bytes]) -> NotImplementedType | None: ...
