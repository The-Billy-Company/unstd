"""Optional atomic validation and native JSON serialization."""

from types import NotImplementedType

def encode(
    obj: object, option: int = 0, spaced: bool = False
) -> bytes | NotImplementedType: ...
