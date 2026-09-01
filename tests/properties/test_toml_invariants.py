"""Properties for TOML read parity and the optional write capability."""

from __future__ import annotations

import io
import json
import tomllib

from hypothesis import given
from hypothesis import strategies as st
import pytest

from unstd import toml


_KEY = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="_-",
        max_codepoint=127,
    ),
    min_size=1,
    max_size=16,
).filter(lambda key: key != "section")
_STRING = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters=" _-./",
        max_codepoint=127,
    ),
    max_size=40,
)
_BOOL = st.booleans()
_INT = st.integers(-(2**63), 2**63 - 1)
_FLOAT = st.floats(
    min_value=-1e100,
    max_value=1e100,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
)
_SCALAR = st.one_of(_BOOL, _INT, _FLOAT, _STRING)
_VALUE = st.one_of(
    _SCALAR,
    st.lists(_BOOL, max_size=6),
    st.lists(_INT, max_size=6),
    st.lists(_FLOAT, max_size=6),
    st.lists(_STRING, max_size=6),
)
_MAPPING = st.dictionaries(_KEY, _VALUE, max_size=8)


def _literal(value: object) -> str:
    """Render the deliberately small generated TOML grammar."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    return repr(value)


def _document(root: dict[str, object], section: dict[str, object]) -> str:
    lines = [f"{key} = {_literal(value)}" for key, value in root.items()]
    if section:
        lines += ["", "[section]"]
        lines += [f"{key} = {_literal(value)}" for key, value in section.items()]
    return "\n".join(lines) + "\n"


@given(root=_MAPPING, section=_MAPPING)
def test_loads_matches_tomllib_for_generated_documents(
    root: dict[str, object],
    section: dict[str, object],
) -> None:
    """The TOML 1.0 stdlib parser is the independent semantic authority."""
    source = _document(root, section)
    assert toml.loads(source) == tomllib.loads(source)


@given(root=_MAPPING, section=_MAPPING)
def test_binary_load_matches_tomllib(
    root: dict[str, object],
    section: dict[str, object],
) -> None:
    """Binary stream behavior remains identical to ``tomllib.load``."""
    payload = _document(root, section).encode()
    assert toml.load(io.BytesIO(payload)) == tomllib.load(io.BytesIO(payload))


@given(obj=_MAPPING)
def test_writer_round_trips_through_tomllib_when_installed(
    obj: dict[str, object],
) -> None:
    """The optional writer must emit a document the stdlib reads faithfully."""
    if not toml._HAVE_TOMLKIT:
        pytest.skip("write path requires the 'toml' extra")

    rendered = toml.dumps(obj)
    assert tomllib.loads(rendered) == obj

    stream = io.StringIO()
    toml.dump(obj, stream)
    assert tomllib.loads(stream.getvalue()) == obj


@given(obj=_MAPPING)
def test_writer_fails_loud_when_optional_backend_is_unavailable(
    obj: dict[str, object],
) -> None:
    """Absence of the optional backend must never become a partial write."""
    stream = io.StringIO()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(toml, "_HAVE_TOMLKIT", False)
        with pytest.raises(ImportError, match=r"unstd\[toml\]"):
            toml.dumps(obj)
        with pytest.raises(ImportError, match=r"unstd\[toml\]"):
            toml.dump(obj, stream)
    assert stream.getvalue() == ""
