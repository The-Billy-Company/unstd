"""Optional seed-faithful CPython list shuffle kernel."""

from collections.abc import Callable

KERNEL_VERSION: int

def shuffle[T](getrandbits: Callable[[int], int], items: list[T]) -> None: ...
def seeded_ints(seeds: list[int], low: int, width: int, /) -> list[int]: ...
