"""YAML 1.2.2 specification examples with values transcribed from the spec.

Each expected value is the spec's own resolution under the core schema
(chapter 10.3) or its stated document structure, not a sample run.
"""

from __future__ import annotations

import datetime as dt
import math

import pytest

from tests.support.environment.backends import require_backend
from unstd import yaml


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


EXAMPLE_10_9 = """\
A null: null
Also a null: # Empty
Not a null: ""
Booleans: [ true, True, false, FALSE ]
Integers: [ 0, 0o7, 0x3A, -19 ]
Floats: [ 0., -0.0, .5, +12e03, -2E+05 ]
Also floats: [ .inf, -.Inf, +.INF, .NAN ]
"""


@pytest.mark.parametrize("reader", ["loads", "parse"])
def test_example_10_9_core_schema_tag_resolution(reader):
    value = getattr(yaml, reader)(EXAMPLE_10_9)
    assert value["A null"] is None
    assert value["Also a null"] is None
    assert value["Not a null"] == ""
    assert [type(item) for item in value["Booleans"]] == [bool] * 4
    assert value["Booleans"] == [True, True, False, False]
    assert all(type(item) is not bool for item in value["Integers"])
    assert value["Integers"] == [0, 7, 58, -19]
    floats = value["Floats"]
    assert all(isinstance(item, float) for item in floats)
    assert floats == [0.0, -0.0, 0.5, 12000.0, -200000.0]
    assert math.copysign(1.0, floats[1]) == -1.0
    special = value["Also floats"]
    assert special[:3] == [math.inf, -math.inf, math.inf]
    assert math.isnan(special[3])


def test_example_2_7_two_documents_in_a_stream():
    source = (
        "# Ranking of 1998 home runs\n---\n- Mark McGwire\n- Sammy Sosa\n"
        "- Ken Griffey\n\n# Team ranking\n---\n- Chicago Cubs\n- St Louis Cardinals\n"
    )
    assert list(yaml.load_all(source)) == [
        ["Mark McGwire", "Sammy Sosa", "Ken Griffey"],
        ["Chicago Cubs", "St Louis Cardinals"],
    ]


def test_example_2_8_play_by_play_feed_with_document_end_markers():
    source = (
        "---\ntime: 20:03:20\nplayer: Sammy Sosa\naction: strike (miss)\n...\n"
        "---\ntime: 20:03:47\nplayer: Sammy Sosa\naction: grand slam\n...\n"
    )
    # Base-60 integers were removed in 1.2; colon-separated times are strings.
    assert list(yaml.load_all(source)) == [
        {"time": "20:03:20", "player": "Sammy Sosa", "action": "strike (miss)"},
        {"time": "20:03:47", "player": "Sammy Sosa", "action": "grand slam"},
    ]


@pytest.mark.parametrize("reader", ["loads", "parse"])
def test_example_2_10_alias_repeats_the_anchored_node(reader):
    source = (
        "---\nhr:\n  - Mark McGwire\n  # Following node labeled SS\n  - &SS Sammy Sosa\n"
        "rbi:\n  - *SS # Subsequent occurrence\n  - Ken Griffey\n"
    )
    assert getattr(yaml, reader)(source) == {
        "hr": ["Mark McGwire", "Sammy Sosa"],
        "rbi": ["Sammy Sosa", "Ken Griffey"],
    }


@pytest.mark.parametrize("reader", ["loads", "parse"])
def test_examples_2_13_to_2_18_scalar_folding(reader):
    source = (
        "literal: |\n  \\//||\\/||\n  // ||  ||__\n"
        "folded: >\n  Mark McGwire's\n  year was crippled\n  by a knee injury.\n"
        "plain:\n  This unquoted scalar\n  spans many lines.\n"
        'quoted: "So does this\n  quoted scalar.\\n"\n'
    )
    assert getattr(yaml, reader)(source) == {
        "literal": "\\//||\\/||\n// ||  ||__\n",
        "folded": "Mark McGwire's year was crippled by a knee injury.\n",
        "plain": "This unquoted scalar spans many lines.",
        "quoted": "So does this quoted scalar.\n",
    }


def test_example_2_22_timestamps_keep_offsets():
    value = yaml.loads(
        "canonical: 2001-12-15T02:59:43.1Z\n"
        "iso8601: 2001-12-14t21:59:43.10-05:00\n"
        "spaced: 2001-12-14 21:59:43.10 -5\n"
        "date: 2002-12-14\n"
    )
    instant = dt.datetime(2001, 12, 15, 2, 59, 43, 100000, tzinfo=dt.UTC)
    for key in ("canonical", "iso8601", "spaced"):
        assert value[key] == instant
        assert value[key].utcoffset() is not None
    assert value["iso8601"].utcoffset() == dt.timedelta(hours=-5)
    assert value["spaced"].utcoffset() == dt.timedelta(hours=-5)
    assert type(value["date"]) is dt.date
    assert value["date"] == dt.date(2002, 12, 14)


@pytest.mark.parametrize("reader", ["loads", "parse"])
def test_explicit_yaml_11_directive_keeps_its_declared_resolution(reader):
    declared = getattr(yaml, reader)("%YAML 1.1\n---\nlegacy: [yes, off, 0777]\n")
    assert declared == {"legacy": [True, False, 511]}
    assert getattr(yaml, reader)("legacy: [yes, off, 0777]\n") == {
        "legacy": ["yes", "off", 777]
    }
