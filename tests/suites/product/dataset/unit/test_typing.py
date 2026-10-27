"""Public annotations resolve for runtime schema and documentation consumers."""

from typing import get_type_hints

from unstd.dataset import Dataset, build


def test_public_annotations_resolve_without_private_imports():
    for operation in (
        build,
        Dataset.__init__,
        Dataset.__getitem__,
        Dataset.take,
        Dataset.iter_rows,
        Dataset.batches,
        Dataset.verify,
        Dataset.close,
        Dataset.__enter__,
        Dataset.__exit__,
    ):
        assert "return" in get_type_hints(operation)
