"""Keyed benchmark receipts keep phase/migration laws and API additions explicit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.profiles import rand_backend as timing
from bench.profiles import rand_keyed as profile


def _source(path, *, keyed=False, endpoint="False", positional=False):
    extra = ", seeds=None" if keyed else ""
    star = "" if positional else "*, "
    signature = f"n, low, high, {star}endpoint={endpoint}{extra}"
    path.write_text(
        f"def ints({signature}): pass\n"
        f"class Generator:\n    def ints(self, {signature}): pass\n"
        "__all__ = ['ints', 'Generator']\nHAVE_NUMPY = False\n"
    )
    return timing._load_source(path)[1]


def test_signature_check_permits_only_the_named_optional_seed_addition(tmp_path):
    before = _source(tmp_path / "before.py")
    keyed = _source(tmp_path / "keyed.py", keyed=True)
    timing._check_sources({"before": before, "keyed": keyed})
    assert set(keyed["optional_public_additions"]) == {"ints", "Generator.ints"}
    changed = _source(tmp_path / "changed.py", keyed=True, endpoint="True")
    with pytest.raises(RuntimeError, match="signatures changed"):
        timing._check_sources({"before": before, "changed": changed})
    with pytest.raises(RuntimeError, match="nonoptional keyed seed argument"):
        _source(tmp_path / "positional.py", keyed=True, positional=True)


def test_keyed_consumer_source_mutation_rejects_the_old_receipt(tmp_path):
    fixture = Path(profile.__file__).resolve().parents[1] / "workloads/rand_keyed.json"
    captured = json.loads(fixture.read_text())
    captured["consumers"]["engine"]["source"] = captured["consumers"]["engine"][
        "source"
    ].replace("siblings[:k]", "siblings")
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(captured))
    with pytest.raises(ValueError, match="AST disagrees"):
        profile._load_fixture(path)


@pytest.mark.parametrize("value", [[0, 2], [1, 5], [1.0, 2], [1]])
def test_count_phase_validation_rejects_wrong_bounds_types_and_count(value):
    with pytest.raises(AssertionError, match="shape"):
        profile._check_counts(value, 2)
