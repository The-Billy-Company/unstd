"""Private immutable numeric-frame validation kernel."""

KERNEL_VERSION: int

def validate(
    data: bytes,
    index_count: int,
    columns: int,
    value_char: str,
    value_order: str,
    index_char: str,
    index_order: str,
    finite: bool,
    allow_empty: bool,
    /,
) -> int: ...
