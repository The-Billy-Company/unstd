"""Every access pattern preserves independently normalized JSON values."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given
from hypothesis import strategies as st

from unstd.dataset import Dataset, build, storage


_TEXT = st.one_of(
    st.text(max_size=40),
    st.sampled_from(["\ud800", "\udfff", "\x00", "\n", "\u2028", "café😀"]),
)
_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**1000), max_value=2**1000)
    | st.floats(allow_nan=False, allow_infinity=False)
    | _TEXT,
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(_TEXT, children, max_size=5)
    ),
    max_leaves=16,
)


@given(st.lists(_VALUES, max_size=16))
def test_all_access_modes_preserve_stdlib_json_values(rows):
    expected = json.loads(json.dumps(rows, ensure_ascii=True, allow_nan=False))
    original = storage._native
    try:
        with TemporaryDirectory(prefix="dataset-values-") as work:
            path = Path(work) / "values.dataset"
            assert build(path, iter(rows)) == len(expected)
            for backend in (original, None):
                storage._native = backend
                with Dataset(path) as dataset:
                    assert list(dataset) == expected
                    assert dataset[:] == expected
                    assert dataset[::-2] == expected[::-2]
                    assert list(dataset.iter_rows(None, None, -1)) == expected[::-1]
                    assert [
                        row for batch in dataset.batches(3) for row in batch
                    ] == expected
                    if expected:
                        indices = [0, -1, len(expected) // 2, 0]
                        assert dataset.take(indices) == [expected[i] for i in indices]
                    dataset.verify()
    finally:
        storage._native = original
