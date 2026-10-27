"""Immutable indexed JSON datasets with portable and optional native row access.

``build`` streams JSON values into an atomically published snapshot. ``Dataset``
opens that snapshot as a read-only sequence, with ordered selections, lazy slices,
and bounded batches. No optional dependency is required.
"""

from unstd.dataset.reader import Dataset
from unstd.dataset.storage import build


__all__ = ["Dataset", "build"]
