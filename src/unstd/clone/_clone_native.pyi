"""Optional exact-builtin structural clone; unsupported trees use Python."""

from collections.abc import Callable
from typing import Any, overload

CLOSED_TEMPORALS: int
MODEL_CONTINUATION: int

def clone(source: Any, /) -> Any: ...
def clone_checked(source: Any, /) -> Any: ...
@overload
def clone_graph(source: Any, memo: dict[int, Any] = ..., /) -> Any: ...
@overload
def clone_graph(
    source: Any,
    memo: dict[int, Any] | None,
    walk: Callable[[Any, dict[int, Any]], Any],
    /,
) -> Any: ...
def clone_dataclass(
    source: Any,
    memo: dict[int, Any],
    walk: Callable[[Any, dict[int, Any]], Any],
    reuse_memo: bool = ...,
    /,
) -> Any: ...
def clone_records(source: Any, cls: type, walk: Callable[[Any], Any], /) -> Any: ...
def clone_record_graph(
    source: Any,
    cls: type,
    walk: Callable[[Any, dict[int, Any]], Any],
    memo: dict[int, Any],
    /,
) -> Any: ...
def clone_walk_graph(
    source: Any,
    walk: Callable[[Any, dict[int, Any]], Any],
    admit: Callable[[type], tuple[bool, Callable[[Any, dict[int, Any]], Any]]],
    memo: dict[int, Any],
    model: Callable[[Any, dict[int, Any]], Any] | None = ...,
    /,
) -> Any: ...
def clone_walk_canonical_graph(
    source: Any,
    walk: Callable[[Any, dict[int, Any]], Any],
    admit: Callable[[type], tuple[bool, Callable[[Any, dict[int, Any]], Any]]],
    memo: dict[int, Any],
    model: Callable[[Any, dict[int, Any]], Any] | None = ...,
    /,
) -> Any: ...
