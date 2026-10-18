"""Mutation searches are reproducible, bounded and independent of global RNG."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import random

import pytest

from tests.tooling.fuzz.mutate import OPERATORS, VERSION, mutations
from tests.tooling.fuzz.registry import Seed


def _seed(name, data):
    return Seed(
        "fixture",
        name + ".bin",
        data,
        hashlib.sha256(data).hexdigest(),
        "Reviewed control",
        "boundary",
        "reviewed synthetic gate fixture",
    )


_CORPUS = (
    _seed("empty", b""),
    _seed("binary", bytes(range(256))),
    _seed("text", b'\x00{"label":"value","index":1}'),
)


def _run(corpus=_CORPUS, **options):
    return list(mutations(corpus, **{"runs": 81, "seed": 123456, **options}))


def test_every_operator_runs_in_versioned_order_with_exact_attribution():
    output = _run()
    assert VERSION == 1
    assert OPERATORS == (
        "flip",
        "insert",
        "delete",
        "duplicate",
        "truncate",
        "splice",
        "selector",
        "boundary",
        "arithmetic",
    )
    assert len(output) == 81
    assert len({identity for identity, _ in output}) == 81
    for index, (identity, data) in enumerate(output):
        version, campaign, iteration, operator, target, filename = identity.split("/")
        assert version == "mutation-v1"
        assert campaign == "123456"
        assert iteration == str(index)
        assert operator == OPERATORS[index % len(OPERATORS)]
        assert target == "fixture"
        assert filename in {seed.file for seed in _CORPUS}
        assert type(data) is bytes
        assert len(data) <= 65536


def test_same_seed_and_corpus_are_repeatable_and_order_independent():
    assert _run() == _run()
    assert _run() == _run(tuple(reversed(_CORPUS)))
    assert _run(seed=1) != _run(seed=2)
    assert _run(runs=27) == _run(runs=81)[:27]


def test_version_one_golden_sequence_detects_unversioned_algorithm_drift():
    output = _run(runs=27)
    encoded = json.dumps(
        [(identity, data.hex()) for identity, data in output], separators=(",", ":")
    ).encode()
    assert (
        hashlib.sha256(encoded).hexdigest()
        == "e81b8cce4d5450f9f33b411bb6d24e3e6b8449c4f64869ba1605e080672c7b8d"
    )


def test_global_random_state_is_neither_read_nor_changed():
    original = random.getstate()
    try:
        random.seed(11)
        first_state = random.getstate()
        first = _run()
        assert random.getstate() == first_state
        random.seed(999999)
        second_state = random.getstate()
        assert _run() == first
        assert random.getstate() == second_state
    finally:
        random.setstate(original)


@pytest.mark.parametrize("budget", [1, 2, 3, 8, 32, 255, 65536])
@pytest.mark.parametrize("empty", [False, True])
def test_every_operator_respects_every_budget_including_empty_corpus_inputs(
    budget, empty
):
    corpus = (_seed("input", b"" if empty else bytes(range(256)) * 256),)
    before = corpus[0].data
    output = _run(corpus, runs=90, max_input_bytes=budget)
    assert {identity.split("/")[3] for identity, _ in output} == set(OPERATORS)
    assert all(type(data) is bytes and len(data) <= budget for _, data in output)
    assert corpus[0].data == before
    assert output == _run(corpus, runs=90, max_input_bytes=budget)


def test_operators_preserve_their_intended_local_transformations():
    original = b"0123456789abcdefghijklmnopqrstuvwxyz"
    source = (_seed("input", original),)
    outcomes = _run(source, runs=90, max_input_bytes=256)
    changed = dict.fromkeys(OPERATORS, False)
    for identity, data in outcomes:
        operator = identity.split("/")[3]
        changed[operator] |= data != original
        if operator == "flip":
            difference = [
                left ^ right for left, right in zip(data, original, strict=True)
            ]
            assert sum(value != 0 for value in difference) == 1
            assert all(value == 0 or value.bit_count() == 1 for value in difference)
        elif operator == "insert":
            assert len(original) < len(data) <= len(original) + 32
        elif operator == "delete":
            assert len(data) <= len(original)
        elif operator == "duplicate":
            assert len(original) < len(data) <= len(original) + 32
        elif operator == "truncate":
            assert original.startswith(data)
        elif operator == "splice":
            assert data.endswith(original)
        elif operator == "selector":
            assert data[1:] == original[1:]
        elif operator == "boundary":
            assert len(original) < len(data) <= len(original) + 8
        else:
            assert len(data) == len(original)
            assert (
                sum(left != right for left, right in zip(data, original, strict=True))
                == 1
            )
    assert all(changed.values())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runs", 0),
        ("runs", -1),
        ("runs", 100001),
        ("runs", True),
        ("runs", 1.0),
        ("seed", -1),
        ("seed", 2**64),
        ("seed", True),
        ("seed", "1"),
        ("max_input_bytes", 0),
        ("max_input_bytes", 65537),
        ("max_input_bytes", True),
        ("max_input_bytes", 1.0),
    ],
)
def test_invalid_campaign_parameters_fail_before_any_output(field, value):
    with pytest.raises(ValueError, match=r"requires|must be|budget"):
        _run(**{field: value})


@pytest.mark.parametrize(
    "corpus",
    [
        (),
        None,
        [],
        1,
        (None,),
        (replace(_CORPUS[0], data=bytearray(b"x")),),
        (_CORPUS[0], _CORPUS[0]),
    ],
)
def test_invalid_corpus_records_and_duplicate_identities_fail_before_output(corpus):
    with pytest.raises(ValueError, match=r"requires|unique"):
        _run(corpus)


@pytest.mark.parametrize("campaign", [0, 2**64 - 1])
def test_unsigned_64_bit_campaign_seed_extrema_are_supported(campaign):
    assert len(_run(seed=campaign, runs=1)) == 1
