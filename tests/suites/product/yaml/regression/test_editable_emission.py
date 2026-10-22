"""The editable writer must not change the values it emits.

The float defect was found by generated round trips. Expected values
are the Python literals below; a plain ruamel safe loader reads the output too.
"""

from __future__ import annotations

import pytest

from tests.support.backends import require_backend
from unstd import yaml


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


def _plain_ruamel(text: str) -> object:
    from ruamel.yaml import YAML

    return YAML(typ="safe", pure=True).load(text)


# A parsed exponent float kept its spelling but the backend truncated the
# re-rendered mantissa: 3.403832445554453e+16 was written as ...452e+16.
@pytest.mark.parametrize(
    "spelling",
    [
        "3.403832445554453e+16",
        "4.842181780694637e+16",
        "8.220385550513652e-26",
        "7.091636858649769e+18",
    ],
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
