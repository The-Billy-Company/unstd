"""The editable writer must not change the values it emits.

Both defects were found by generated round trips (properties/). Expected values
are the Python literals below; a plain ruamel safe loader reads the output too.
"""

from __future__ import annotations

import pytest

from tests.support.environment.backends import require_backend
from unstd import yaml


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


def _plain_ruamel(text: str) -> object:
    from ruamel.yaml import YAML

    return YAML(typ="safe", pure=True).load(text)


# NEL (U+0085) is printable YAML 1.2 content. The backend reader folds it like
# a line break outside double quotes, so single-quoted emission lost it.
@pytest.mark.parametrize(
    "value",
    ["\x85", "a\x85b", "\x85\n", {"key\x85": ["\x85 ", "plain"]}],
    ids=["alone", "inner", "with-newline", "key-and-items"],
)
def test_nel_survives_dumps(value):
    text = yaml.dumps(value)
    assert yaml.loads(text) == value, text
    assert yaml.parse(text) == value, text
    assert _plain_ruamel(text) == value, text


def test_nel_in_a_preserved_single_quoted_scalar_is_escaped():
    document = yaml.parse("label: 'plain'\n")
    document["label"] = type(document["label"])("a\x85b")
    text = yaml.dumps(document)
    assert yaml.loads(text) == {"label": "a\x85b"}, text


# A parsed exponent float kept its spelling but the backend truncated the
# re-rendered mantissa: 3.403832445554453e+16 was written as ...452e+16.
@pytest.mark.parametrize(
    "spelling",
    ["3.403832445554453e+16", "8.220385550513652e-26", "7.091636858649769e+18"],
)
def test_parsed_exponent_floats_keep_their_value(spelling):
    expected = float(spelling)
    text = yaml.dumps(yaml.parse(f"value: {spelling}\n"))
    assert yaml.loads(text) == {"value": expected}, text
    assert _plain_ruamel(text) == {"value": expected}, text


def test_rewritten_float_keeps_its_anchor_and_alias():
    text = yaml.dumps(yaml.parse("x: &f 3.403832445554453e+16\ny: *f\n"))
    assert "&f" in text
    assert "*f" in text
    assert yaml.loads(text) == {"x": 3.403832445554453e16, "y": 3.403832445554453e16}


@pytest.mark.parametrize("spelling", ["+12e03", "6.02214076e23", "-2E+05", "0."])
def test_exact_float_spellings_are_still_retained(spelling):
    assert yaml.dumps(yaml.parse(f"value: {spelling}\n")) == f"value: {spelling}\n"
