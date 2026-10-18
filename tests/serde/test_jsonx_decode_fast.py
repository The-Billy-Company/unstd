"""Differential evidence for the decoder's conservative acceptance proof.

The oracle is CPython's running json module. The proof admits only builtin
trees without floats; no rounded backend result may escape through this path.
See orjson's documented integer bounds and numeric constructors:
https://github.com/ijl/orjson#int and
https://github.com/ijl/orjson/blob/master/src/deserialize/backend/yyjson.rs.
"""

from __future__ import annotations

import json
from typing import Any

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd.serde._json_decode import _decode_eligible, _decoded_exact


@pytest.mark.parametrize("container", [str, bytes])
@pytest.mark.parametrize("count", [0, 1, 7, 8, 9, 63, 64, 65, 1000])
@pytest.mark.parametrize("padding", ["", " " * 4096], ids=["small", "large"])
def test_source_bound_counts_every_raw_opener(
    container: type[str] | type[bytes], count: int, padding: str
) -> None:
    # Half the source openers are objects, half are arrays; neither count may
    # independently consume the entire shared budget.
    document = padding + "[" * (count // 2) + "{" * (count - count // 2)
    source = document if container is str else document.encode()
    assert _decode_eligible(source) is (count <= (64 if padding else 8))


def test_openers_inside_strings_are_conservatively_counted() -> None:
    assert not _decode_eligible(json.dumps("[" * 65))
    assert not _decode_eligible(json.dumps("{" * 65))
    assert _decode_eligible(json.dumps("[]{}" * 4))


@pytest.mark.parametrize("value", [bytearray(b"{}"), memoryview(b"{}"), None, 1])
def test_nonexact_source_types_use_stdlib(value: object) -> None:
    assert not _decode_eligible(value)


def test_source_subclasses_are_not_inspected() -> None:
    class Text(str):
        def find(self, *_args: Any, **_kwargs: Any) -> int:
            pytest.fail("the eligibility proof inspected a text subclass")

    class Binary(bytes):
        def find(self, *_args: Any, **_kwargs: Any) -> int:
            pytest.fail("the eligibility proof inspected a bytes subclass")

    assert not _decode_eligible(Text("{}"))
    assert not _decode_eligible(Binary(b"{}"))


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "1e999 NaN 18446744073709551616",
        0,
        -(2**63),
        2**64 - 1,
        [],
        {},
        [True, None, 0, "text"],
        {"list": [1, {"other": "value"}], "empty": {}},
    ],
)
def test_proof_accepts_exact_builtin_trees(value: object) -> None:
    assert _decoded_exact(value)


@pytest.mark.parametrize("value", [0.0, -0.0, 1.0, float("inf"), float("nan")])
def test_proof_rejects_every_float_at_every_container_position(value: float) -> None:
    assert not _decoded_exact(value)
    assert not _decoded_exact([value, 1])
    assert not _decoded_exact([1, value])
    assert not _decoded_exact({"first": value, "second": 1})
    assert not _decoded_exact({"first": 1, "second": [{"nested": value}]})


def test_result_proof_does_not_use_python_recursion() -> None:
    value: object = 1
    for _ in range(5000):
        value = [value]
    assert _decoded_exact(value)


def test_result_subclasses_are_not_inspected() -> None:
    class Items(list[Any]):
        def __iter__(self) -> Any:
            pytest.fail("the result proof inspected a list subclass")

    class Mapping(dict[str, Any]):
        def values(self) -> Any:
            pytest.fail("the result proof inspected a dictionary subclass")

    assert not _decoded_exact(Items([1]))
    assert not _decoded_exact(Mapping(a=1))


@pytest.mark.parametrize(
    "document",
    [
        "1.0",
        "-0.0",
        "1e0",
        "-0e0",
        "1e-4000",
        "-1e-4000",
        "4.9406564584124654e-324",
        "2.4703282292062327e-324",
        "1.00000000000000011102230246251565404236316680908203125",
        "1.00000000000000011102230246251565404236316680908203126",
        "18446744073709551616",
        "18446744073709551617",
        "-9223372036854775809",
        "9" * 308,
    ],
)
def test_backend_rounding_is_rejected_before_the_result_is_exposed(
    document: str,
) -> None:
    orjson = pytest.importorskip("orjson")
    # This is deliberately a post-decode check: these tokens are often
    # accepted by both codecs, with different numeric types or float bits.
    try:
        decoded = orjson.loads(document)
    except orjson.JSONDecodeError:
        return
    assert _decode_eligible(document)
    assert not _decoded_exact(decoded)


@pytest.mark.parametrize(
    "document",
    [
        '{"x":1.234567890123456789,"x":1}',
        '{"x":18446744073709551617,"x":1}',
        '{"first":1,"other":2,"first":3}',
        '{"é":1,"\\u00e9":2}',
        '{"x":[1.234567890123456789],"x":{"nested":1}}',
    ],
)
def test_discarded_duplicate_values_do_not_change_the_final_tree(document: str) -> None:
    orjson = pytest.importorskip("orjson")
    actual, expected = orjson.loads(document), json.loads(document)
    assert _decode_eligible(document)
    assert _decoded_exact(actual)
    assert actual == expected
    assert list(actual) == list(expected)


_SCALARS = (
    st.none()
    | st.booleans()
    | st.integers(-(2**63), 2**64 - 1)
    | st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=100)
)
_TREES = st.recursive(
    _SCALARS,
    lambda children: (
        st.lists(children, max_size=10)
        | st.dictionaries(st.text(max_size=20), children, max_size=10)
    ),
    max_leaves=40,
)


@given(value=_TREES, ensure_ascii=st.booleans(), binary=st.booleans())
def test_every_admitted_generated_tree_matches_stdlib(
    value: object, ensure_ascii: bool, binary: bool
) -> None:
    orjson = pytest.importorskip("orjson")
    document = json.dumps(value, ensure_ascii=ensure_ascii)
    source = document.encode() if binary else document
    if not _decode_eligible(source):
        return
    try:
        actual = orjson.loads(source)
    except orjson.JSONDecodeError:
        return  # The wrapper must let stdlib handle its wider Unicode domain.
    assert _decoded_exact(actual)
    assert actual == json.loads(source)
    # Re-encoding catches key order and nested builtin types as well as values.
    assert json.dumps(actual) == json.dumps(json.loads(source))
