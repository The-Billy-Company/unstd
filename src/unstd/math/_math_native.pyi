"""Private optional ndarray kernels, independent of NumPy's C ABI."""

from types import NotImplementedType
from typing import Any

from numpy.typing import NDArray

KERNEL_VERSION: int

def within(
    actual: object, expected: object, atol: object, /
) -> bool | NotImplementedType: ...
def topk(values: object, k: object, /) -> NDArray[Any] | NotImplementedType: ...
def normalize_rows(
    values: object, eps: float, dtype: str, /
) -> NDArray[Any] | NotImplementedType: ...
def cosine_rows(
    values: object, query: object, eps: float, dtype: str, /
) -> NDArray[Any] | NotImplementedType: ...
def mean_square(values: object, dtype: str, /) -> NDArray[Any] | NotImplementedType: ...
def weighted_mean(
    values: object, weights: object, dtype: str, /
) -> NDArray[Any] | NotImplementedType: ...
def grouped_mean(
    values: object, labels: object, groups: object, /
) -> NDArray[Any] | NotImplementedType: ...
def frame(
    values: object, length: object, hop: object, /
) -> NDArray[Any] | NotImplementedType: ...
