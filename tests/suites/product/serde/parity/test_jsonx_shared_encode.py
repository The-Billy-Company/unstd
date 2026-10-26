"""Shared flat branches retain bounded admission and per-call JSON spellings."""

from __future__ import annotations

import json
import math
import sys

import pytest

from unstd.serde import jsonx


native = pytest.importorskip("unstd.serde._json_native")
pytest.importorskip("orjson")


def _compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _native(value: object) -> object:
    return native.dumpb(value, False, None, False)


@pytest.mark.parametrize(
    "leaf",
    [
        [],
        [None, True, False, 1.5, -0.0, 1e-5, "café😀\x7f", 'quote"\\'],
        {"score": 1.5, "zero": -0.0, "text": "café😀\x7f"},
        list(range(256)),
    ],
)
def test_shared_flat_values_are_admitted_and_match_each_format(leaf):
    value = [leaf] * 64
    assert _native(value) == _compact(value)
    assert jsonx.dumpb(value) == _compact(value)
    for ascii_output in (False, True):
        for separators in (None, (",", ":")):
            assert jsonx.dumps(
                value, ensure_ascii=ascii_output, separators=separators
            ) == json.dumps(value, ensure_ascii=ascii_output, separators=separators)


@pytest.mark.parametrize("repeat", [1020, 1021])
def test_each_alias_spends_its_full_logical_visit_budget(repeat):
    leaf = [1.5] * 256
    value = [leaf] * repeat
    expected = _compact(value)
    assert len(expected) < 1024 * 1024
    assert 1 + repeat * (1 + len(leaf)) == (262141 if repeat == 1020 else 262398)
    result = _native(value)
    if repeat == 1020:
        assert result == expected
    else:
        assert result is NotImplemented
    assert jsonx.dumpb(value) == expected


def test_aliases_at_another_depth_are_revalidated_before_copying():
    leaf = [1.5, "escaped\n", "café"] * 16
    nested = leaf
    for _ in range(14):
        nested = [nested]
    value = [leaf] * 128 + [nested]
    assert _native(value) == _compact(value)
    too_deep = [leaf] * 128 + [[nested]]
    assert _native(too_deep) is NotImplemented
    assert jsonx.dumpb(too_deep) == _compact(too_deep)


def test_private_output_limit_preserves_the_complete_public_result():
    leaf = [1.5, "x" * 256] * 16
    value = [leaf] * 300
    expected = _compact(value)
    assert len(expected) > 1024 * 1024
    assert 1 + len(value) * (1 + len(leaf)) < 262144
    assert _native(value) is NotImplemented
    assert jsonx.dumpb(value) == expected


def test_declined_shared_writer_keeps_the_existing_wide_integer_backend():
    leaf = [2**40, -(2**40)] * 16
    value = [leaf] * 128
    assert _native(value) == _compact(value)


@pytest.mark.parametrize("leaf", [[math.inf], [math.nan], ["\ud800"], {1: 1.5}])
def test_declined_shared_values_keep_the_stdlib_result_or_error(leaf):
    value = [leaf, leaf]
    assert _native(value) is NotImplemented
    if leaf == ["\ud800"]:
        for function in (_compact, jsonx.dumpb):
            with pytest.raises(UnicodeEncodeError):
                function(value)
    else:
        assert jsonx.dumpb(value) == _compact(value)


def test_cycles_are_never_completed_or_cached():
    leaf = [1.5] * 31
    leaf.append(leaf)
    value = [leaf] * 128
    assert _native(value) is NotImplemented
    for function in (json.dumps, jsonx.dumps, jsonx.dumpb):
        with pytest.raises(ValueError, match="Circular reference"):
            function(value)


def test_source_mutations_between_calls_are_visible_in_every_alias():
    leaf = {"score": 1.5, "text": "first", **{str(index): index for index in range(30)}}
    value = [leaf] * 128
    first = jsonx.dumpb(value)
    leaf["score"] = -0.0
    leaf["text"] = 'second "quoted" café'
    assert first != jsonx.dumpb(value)
    assert jsonx.dumpb(value) == _compact(value)
    leaf.clear()
    assert jsonx.dumpb(value) == _compact(value)


def test_completed_and_declined_caches_release_their_owned_references():
    leaf = [1.5, " ".join(("not", "interned", "101"))] * 16
    value = [leaf] * 128
    before = (sys.getrefcount(leaf), sys.getrefcount(leaf[1]))
    for _ in range(20):
        assert _native(value) == _compact(value)
    assert (sys.getrefcount(leaf), sys.getrefcount(leaf[1])) == before
    value.append([object()])
    for _ in range(20):
        assert _native(value) is NotImplemented
    assert (sys.getrefcount(leaf), sys.getrefcount(leaf[1])) == before


def test_many_distinct_branches_and_aliases_survive_cache_replacement():
    leaves = [
        [(index * 7919) % 20000 - 10000, index / 97, "café"] * 10 + [0, 1]
        for index in range(96)
    ]
    value = [leaves[0], leaves[0], *leaves, *reversed(leaves)]
    assert _native(value) == _compact(value)
    assert jsonx.dumps(value) == json.dumps(value)


def test_shared_wide_dictionary_uses_the_same_exact_scalar_spellings():
    leaf = {str(index): index / 7 for index in range(32)}
    value = [leaf] * 128
    assert _native(value) == _compact(value)
    assert jsonx.dumps(value) == json.dumps(value)
