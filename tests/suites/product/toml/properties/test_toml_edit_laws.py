r"""Generated temporal/Unicode writes and stateful edit sequences.

Writes are restricted to values TOML 1.0 can represent: minute-precision UTC
offsets and offset-free times. Plain-data strings include ESC, which the writer
must spell ``\u001b``. Values assigned into a parsed document become tomlkit
items that keep tomlkit's 1.1-only ``\e``, so edit strings exclude ESC.
``tomllib`` is the independent reader and a plain ``dict`` is the reference
model for edits.
"""

from __future__ import annotations

import datetime as dt
import tomllib

from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
import pytest

from tests.support.environment.backends import require_backend
from unstd import toml


@pytest.fixture(autouse=True)
def tomlkit_backend() -> None:
    require_backend("tomlkit")


_OFFSET = st.integers(-(24 * 60 - 1), 24 * 60 - 1).map(
    lambda m: dt.timezone(dt.timedelta(minutes=m))
)
_CHARS = st.characters(exclude_categories=("Cs",))
_TEXT = st.text(_CHARS | st.just("\x1b"))
_KEY = st.text(_CHARS | st.just("\x1b"), max_size=8)
_EDIT_TEXT = st.text(
    st.characters(exclude_categories=("Cs",), exclude_characters="\x1b")
)
_LEAF = st.one_of(
    st.datetimes(timezones=st.none() | _OFFSET),
    st.dates(),
    st.times(),
    st.integers(-(2**63), 2**63 - 1),
    st.floats(allow_nan=False),
    _TEXT,
)


@given(st.dictionaries(_KEY, _LEAF | st.lists(_LEAF, max_size=4), max_size=6))
def test_common_dialect_values_round_trip_through_tomllib(
    obj: dict[str, object],
) -> None:
    back = tomllib.loads(toml.dumps(obj))
    assert back == obj
    for key, value in obj.items():
        if isinstance(value, dt.datetime | dt.time):
            assert back[key].utcoffset() == value.utcoffset()


@given(st.dictionaries(_KEY, _LEAF, max_size=6))
def test_parse_of_written_document_reemits_it_exactly(obj: dict[str, object]) -> None:
    text = toml.dumps(obj)
    assert toml.dumps(toml.parse(text)) == text


_NAME = st.sampled_from(["alpha", "beta", "gamma", "delta"])
_VALUE = st.one_of(st.integers(-(2**63), 2**63 - 1), st.booleans(), _EDIT_TEXT)
HEADER = "# keep this header\n"


class EditMachine(RuleBasedStateMachine):
    """Edits on a parsed document must track a plain dict and keep its header."""

    def __init__(self) -> None:
        super().__init__()
        self.doc = toml.parse(HEADER + "[table]\n")
        self.model: dict[str, dict[str, object]] = {"table": {}}

    @rule(key=_NAME, value=_VALUE)
    def set_root(self, key: str, value: object) -> None:
        self.doc[key] = value
        self.model[key] = value  # type: ignore[assignment]

    @rule(key=_NAME, value=_VALUE)
    def set_nested(self, key: str, value: object) -> None:
        self.doc["table"][key] = value
        self.model["table"][key] = value

    @rule(key=_NAME)
    def delete_nested(self, key: str) -> None:
        if key in self.model["table"]:
            del self.doc["table"][key]
            del self.model["table"][key]
        else:
            with pytest.raises(KeyError):
                del self.doc["table"][key]

    @invariant()
    def matches_model_and_keeps_header(self) -> None:
        text = toml.dumps(self.doc)
        assert text.startswith(HEADER)
        assert tomllib.loads(text) == self.model


TestEditMachine = EditMachine.TestCase
