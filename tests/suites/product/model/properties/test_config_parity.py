"""Generated field layouts and payloads against explicitly configured Pydantic twins.

Each example builds the same fields twice: once on the ``unstd.model`` base and
once on plain Pydantic with the documented configuration spelled out. Aliases
may collide with other field names. Outcomes, error types and locations,
dumps, fields-set, schemas and hash/frozen behavior must be identical.
"""

from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from pydantic_settings import BaseSettings, SettingsConfigDict

from unstd.model import FrozenModel, StrictModel, StrictSettings


_NAMES = ("a", "b", "c")
_KEYS = (*_NAMES, "from", "x-y", "zzz")
_TYPES = {"int": int, "str": str, "ints": list[int], "optional": int | None}
_STRICT = ConfigDict(extra="forbid", validate_by_name=True)


@st.composite
def _layouts(draw: st.DrawFn) -> dict[str, tuple[Any, Any]]:
    names = draw(st.lists(st.sampled_from(_NAMES), min_size=1, unique=True))
    fields = {}
    for name in names:
        kind = draw(st.sampled_from(sorted(_TYPES)))
        alias = draw(st.none() | st.sampled_from(_KEYS[:5]))
        options: dict[str, Any] = {} if alias is None else {"alias": alias}
        if draw(st.booleans()):
            options["default"] = None if kind == "optional" else _TYPES[kind]()
        fields[name] = (_TYPES[kind], Field(**options))
    return fields


_VALUE = st.one_of(
    st.integers(-3, 3),
    st.sampled_from(["1", "x", "", " 2 "]),
    st.lists(st.integers(-2, 2) | st.just("3"), max_size=2),
    st.none(),
    st.floats(allow_nan=False, allow_infinity=False, width=16),
)
_PAYLOAD = st.dictionaries(st.sampled_from(_KEYS), _VALUE, max_size=5)


def _outcome(model: type[BaseModel], payload: dict[str, object]) -> object:
    try:
        made = model.model_validate(payload)
    except ValidationError as error:
        return "error", sorted(
            (e["type"], e["loc"], repr(e["input"])) for e in error.errors()
        )
    except KeyError as error:
        # pydantic-settings 2.15 crashes when an alias names another field.
        return "raised", type(error), str(error)
    return (
        "ok",
        made.model_dump(),
        made.model_dump(by_alias=True),
        made.model_fields_set,
        made.model_extra,
    )


def _pair(
    base: type[BaseModel], config: ConfigDict, fields: dict[str, Any]
) -> tuple[type[BaseModel], type[BaseModel]]:
    ours = create_model("Shape", __base__=base, **fields)
    twin = create_model("Shape", __config__=config, **fields)
    return ours, twin


@given(fields=_layouts(), payload=_PAYLOAD)
def test_strict_model_matches_explicit_config(
    fields: dict[str, Any], payload: dict[str, object]
) -> None:
    ours, twin = _pair(StrictModel, _STRICT, fields)
    assert _outcome(ours, payload) == _outcome(twin, payload)
    for mode in ("validation", "serialization"):
        schema = ours.model_json_schema(mode=mode)
        assert schema == twin.model_json_schema(mode=mode)
        assert schema["additionalProperties"] is False


@given(fields=_layouts(), payload=_PAYLOAD, value=_VALUE)
def test_frozen_model_matches_explicit_config(
    fields: dict[str, Any], payload: dict[str, object], value: object
) -> None:
    ours, twin = _pair(FrozenModel, ConfigDict(**_STRICT, frozen=True), fields)
    result = _outcome(ours, payload)
    assert result == _outcome(twin, payload)
    if result[0] != "ok":
        return
    made, control = ours.model_validate(payload), twin.model_validate(payload)
    assert _hash_outcome(made) == _hash_outcome(control)
    for name in fields:
        assert _assign_outcome(made, name, value) == _assign_outcome(
            control, name, value
        )
        assert getattr(made, name) == getattr(control, name)


def _hash_outcome(made: BaseModel) -> object:
    try:
        return "hash", hash(made) == hash(made.model_copy())
    except TypeError as error:
        return "unhashable", str(error)


def _assign_outcome(made: BaseModel, name: str, value: object) -> object:
    try:
        setattr(made, name, value)
    except ValidationError as error:
        return [(e["type"], e["loc"]) for e in error.errors()]
    return "assigned"


def _init_only(
    cls: type[BaseSettings],
    settings_cls: type[BaseSettings],
    *,
    init_settings: Any,
    **_sources: Any,
) -> tuple[Any, ...]:
    return (init_settings,)


# Settings validation runs __init__, which would otherwise read the environment.
class _OursSettings(StrictSettings):
    settings_customise_sources = classmethod(_init_only)


class _TwinSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", validate_by_name=True)
    settings_customise_sources = classmethod(_init_only)


@given(fields=_layouts(), payload=_PAYLOAD)
def test_settings_validation_matches_explicit_config(
    fields: dict[str, Any], payload: dict[str, object]
) -> None:
    ours = create_model("Settings", __base__=_OursSettings, **fields)
    twin = create_model("Settings", __base__=_TwinSettings, **fields)
    assert _outcome(ours, payload) == _outcome(twin, payload)
    assert ours.model_json_schema() == twin.model_json_schema()
