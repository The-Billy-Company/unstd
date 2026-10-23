"""Optional bounded stream batching and bearer-envelope parsing."""

from collections.abc import Callable, Iterable
from types import NotImplementedType

from blake3 import blake3

def absorb(hasher: blake3, chunks: Iterable[bytes]) -> NotImplementedType | None: ...
def bind_token_verifier(
    fallback: Callable[..., bytes | None],
) -> Callable[[bytes, str, int | None], bytes | None]: ...
