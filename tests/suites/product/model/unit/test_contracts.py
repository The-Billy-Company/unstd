"""Documented ``unstd.model`` contracts against explicitly configured Pydantic twins.

Every comparison model below states the documented configuration directly on
``pydantic.BaseModel`` or ``pydantic_settings.BaseSettings``. The strict bases
must agree with those twins on validation outcomes, errors, dumps and schemas;
fixture-defined values pin what the documentation promises outright.
"""

import enum
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    create_model,
)
from pydantic_settings import BaseSettings
import pytest

from unstd.model import FrozenModel, StrictModel, StrictSettings


_STRICT = ConfigDict(extra="forbid", validate_by_name=True)


def _errors(error: ValidationError) -> list[tuple[str, tuple[int | str, ...]]]:
    return sorted((e["type"], e["loc"]) for e in error.errors())


def _outcome(model: type[BaseModel], payload: object) -> object:
    try:
        made = model.model_validate(payload)
    except ValidationError as error:
        return ("error", _errors(error))
    return ("ok", made.model_dump(), made.model_fields_set)


# ── exact configuration ───────────────────────────────────────────────────────


def test_configs_are_exactly_the_documented_dictionaries() -> None:
    assert dict(StrictModel.model_config) == {
        "extra": "forbid",
        "validate_by_name": True,
    }
    assert dict(FrozenModel.model_config) == {
        "extra": "forbid",
        "validate_by_name": True,
        "frozen": True,
    }
    assert dict(StrictSettings.model_config) == {
        **BaseSettings.model_config,
        "extra": "ignore",
        "validate_by_name": True,
    }


def test_strict_model_is_not_pydantic_strict_mode() -> None:
    class Lax(StrictModel):
        n: int
        flag: bool
        ratio: float

    class Strict(BaseModel):
        model_config = ConfigDict(**_STRICT, strict=True)
        n: int
        flag: bool
        ratio: float

    payload = {"n": "7", "flag": "yes", "ratio": 2}
    made = Lax.model_validate(payload)
    assert (made.n, made.flag, made.ratio) == (7, True, 2.0)
    assert (type(made.n), type(made.ratio)) == (int, float)
    with pytest.raises(ValidationError) as strict:
        Strict.model_validate(payload)
    assert _errors(strict.value) == [
        ("bool_type", ("flag",)),
        ("int_type", ("n",)),
    ]


class _Shade(enum.Enum):
    DARK = "dark"


def test_deliberately_unset_options_keep_pydantic_defaults() -> None:
    class Body(StrictModel):
        name: str
        shade: _Shade
        count: int = 0
        frm: str = Field(default="", alias="from")

    body = Body(name="  padded  ", shade="dark", frm="x")
    assert body.name == "  padded  "  # no str_strip_whitespace
    assert body.shade is _Shade.DARK  # no use_enum_values
    assert body.model_dump() == {
        "name": "  padded  ",
        "shade": _Shade.DARK,
        "count": 0,
        "frm": "x",
    }  # serialization stays by field name
    assert body.model_dump(by_alias=True)["from"] == "x"
    body.count = "not validated"  # type: ignore[assignment]  # no validate_assignment
    assert body.count == "not validated"


# ── unknown fields across nested trees ───────────────────────────────────────


class _Plain(BaseModel):
    value: int = 0


class _Leaf(StrictModel):
    value: int = 0


class _Tree(StrictModel):
    leaf: _Leaf = _Leaf()
    leaves: list[_Leaf] = Field(default_factory=list)
    by_key: dict[str, _Leaf] = Field(default_factory=dict)
    plain: _Plain = _Plain()


def test_every_nested_strict_level_reports_its_extra_location() -> None:
    payload = {
        "leaf": {"value": 1, "a": 1},
        "leaves": [{"value": 2}, {"b": 2}],
        "by_key": {"k": {"c": 3}},
        "plain": {"value": 4, "ignored": 4},
        "top": 5,
    }
    with pytest.raises(ValidationError) as caught:
        _Tree.model_validate(payload)
    assert _errors(caught.value) == [
        ("extra_forbidden", ("by_key", "k", "c")),
        ("extra_forbidden", ("leaf", "a")),
        ("extra_forbidden", ("leaves", 1, "b")),
        ("extra_forbidden", ("top",)),
    ]


def test_a_plain_nested_model_keeps_its_own_ignore_policy() -> None:
    tree = _Tree.model_validate({"plain": {"value": 4, "ignored": 4}})
    assert tree.plain.model_dump() == {"value": 4}
    assert tree.model_fields_set == {"plain"}


def test_subclass_override_is_inherited_without_losing_name_population() -> None:
    class Loose(StrictModel, extra="allow"):
        frm: str = Field(alias="from")

    class LooseChild(Loose):
        pass

    for model in (Loose, LooseChild):
        made = model.model_validate({"frm": "x", "kept": [1]})
        assert made.frm == "x"
        assert made.model_extra == {"kept": [1]}
        assert model.model_json_schema()["additionalProperties"] is True

    class Restricted(LooseChild, extra="forbid"):
        pass

    with pytest.raises(ValidationError) as caught:
        Restricted.model_validate({"from": "x", "kept": 1})
    assert _errors(caught.value) == [("extra_forbidden", ("kept",))]


# ── aliases, including conflicts with other field names ──────────────────────


def test_alias_and_name_together_reject_the_name_as_extra() -> None:
    class Ours(StrictModel):
        frm: str = Field(alias="from")

    class Twin(BaseModel):
        model_config = _STRICT
        frm: str = Field(alias="from")

    for payload in (
        {"from": "x"},
        {"frm": "y"},
        {"from": "x", "frm": "y"},
        {},
        {"FROM": "x"},
    ):
        assert _outcome(Ours, payload) == _outcome(Twin, payload), payload
    with pytest.raises(ValidationError) as caught:
        Ours.model_validate({"from": "x", "frm": "y"})
    assert _errors(caught.value) == [("extra_forbidden", ("frm",))]


def test_alias_naming_another_field_feeds_both_fields() -> None:
    class Ours(StrictModel):
        a: int = Field(alias="b")
        b: int = 0

    class Twin(BaseModel):
        model_config = _STRICT
        a: int = Field(alias="b")
        b: int = 0

    for payload in ({"b": 1}, {"a": 2}, {"a": 2, "b": 3}, {}, {"b": "x"}):
        assert _outcome(Ours, payload) == _outcome(Twin, payload), payload
    made = Ours.model_validate({"b": 1})
    assert (made.a, made.b) == (1, 1)


def test_validation_alias_choices_and_serialization_alias() -> None:
    field = {
        "validation_alias": AliasChoices("PORT", "listen_port"),
        "serialization_alias": "listenPort",
    }
    ours_model = create_model("Ours", __base__=StrictModel, port=(int, Field(**field)))
    twin_model = create_model("Twin", __config__=_STRICT, port=(int, Field(**field)))

    for key, value in {"PORT": 1, "listen_port": 2, "port": 3}.items():
        assert _outcome(ours_model, {key: value}) == ("ok", {"port": value}, {"port"})
    serialized_key = _outcome(ours_model, {"listenPort": 4})
    assert serialized_key == _outcome(twin_model, {"listenPort": 4})
    assert serialized_key[0] == "error"
    assert ("extra_forbidden", ("listenPort",)) in serialized_key[1]
    assert ours_model(port=5).model_dump(by_alias=True) == {"listenPort": 5}


# ── JSON schema ──────────────────────────────────────────────────────────────


def _body(base: type[BaseModel], config: ConfigDict | None) -> type[BaseModel]:
    leaf = create_model("Leaf", __base__=base, __config__=config, value=(int, 0))
    return create_model(
        "Body",
        __base__=base,
        __config__=config,
        frm=(str, Field(alias="from")),
        leaf=(leaf, ...),
        plain=(_Plain, ...),
    )


def test_schema_matches_explicit_twin_in_both_modes() -> None:
    ours_model, twin_model = _body(StrictModel, None), _body(BaseModel, _STRICT)
    for mode in ("validation", "serialization"):
        ours = ours_model.model_json_schema(mode=mode)
        assert ours == twin_model.model_json_schema(mode=mode)
        assert ours["additionalProperties"] is False
        assert ours["$defs"]["Leaf"]["additionalProperties"] is False
        assert "additionalProperties" not in ours["$defs"]["_Plain"]
        assert set(ours["properties"]) == {"from", "leaf", "plain"}


def test_frozen_and_settings_schemas_follow_their_extra_policy() -> None:
    class Point(FrozenModel):
        x: int

    class Settings(StrictSettings):
        port: int = 1

    class ForbidSettings(BaseSettings):
        port: int = 1

    assert Point.model_json_schema()["additionalProperties"] is False
    assert "additionalProperties" not in Settings.model_json_schema()
    assert ForbidSettings.model_json_schema()["additionalProperties"] is False


# ── frozen models holding mutable or unhashable values ──────────────────────


def test_frozen_model_rejects_assignment_and_deletion_with_exact_errors() -> None:
    class Point(FrozenModel):
        x: int
        tags: tuple[str, ...] = ()

    point = Point(x=1, tags=("a",))
    for mutate in (lambda: setattr(point, "x", 2), lambda: delattr(point, "x")):
        with pytest.raises(ValidationError) as caught:
            mutate()
        assert _errors(caught.value) == [("frozen_instance", ("x",))]
    assert point.x == 1
    assert hash(point) == hash(Point(x=1, tags=("a",)))
    assert {point: "v"}[Point(x=1, tags=("a",))] == "v"
    moved = point.model_copy(update={"x": 3})
    assert (moved.x, point.x) == (3, 1)


@pytest.mark.parametrize(
    ("annotation", "value"),
    [(list[int], [1]), (dict[str, int], {"k": 1}), (set[int], {1})],
)
def test_frozen_model_with_unhashable_values_raises_like_its_twin(
    annotation: Any, value: object
) -> None:
    ours_model = create_model("Ours", __base__=FrozenModel, v=(annotation, ...))
    twin_model = create_model(
        "Twin", __config__=ConfigDict(**_STRICT, frozen=True), v=(annotation, ...)
    )
    ours, twin = ours_model(v=value), twin_model(v=value)
    with pytest.raises(TypeError) as expected:
        hash(twin)
    with pytest.raises(type(expected.value), match="unhashable"):
        hash(ours)


def test_frozen_model_retains_and_exposes_mutable_field_contents() -> None:
    class Bag(FrozenModel):
        items: list[int]

    shared = [1]
    bag = Bag(items=shared)
    bag.items.append(2)
    assert bag.items == [1, 2]
    assert bag == Bag(items=[1, 2])
    assert bag != Bag(items=[1])


def test_frozen_model_holding_a_mutable_strict_model_is_unhashable() -> None:
    class Inner(StrictModel):
        n: int

    class FrozenInner(FrozenModel):
        n: int

    class Outer(FrozenModel):
        inner: Inner

    class SafeOuter(FrozenModel):
        inner: FrozenInner

    with pytest.raises(TypeError, match="unhashable"):
        hash(Outer(inner=Inner(n=1)))
    assert hash(SafeOuter(inner=FrozenInner(n=1))) == hash(
        SafeOuter(inner=FrozenInner(n=1))
    )
    outer = Outer(inner=Inner(n=1))
    outer.inner.n = 2  # the nested model is not frozen by its container
    assert outer.inner.n == 2


# ── settings construction without environment input ─────────────────────────


class _Group(StrictModel):
    host: str = "localhost"


class _Settings(StrictSettings):
    port: int = Field(default=1, validation_alias="SERVICE_PORT")
    group: _Group = _Group()

    # Validation runs __init__; only initializer input may reach these cases.
    @classmethod
    def settings_customise_sources(
        cls, settings_cls: type[BaseSettings], *, init_settings: Any, **_sources: Any
    ) -> tuple[Any, ...]:
        return (init_settings,)


def test_settings_validation_ignores_root_extras_but_nested_groups_forbid() -> None:
    made = _Settings.model_validate({"port": 2, "ambient": "x", "group": {"host": "h"}})
    assert made.model_dump() == {"port": 2, "group": {"host": "h"}}
    assert _Settings.model_validate({"SERVICE_PORT": 3}).port == 3
    with pytest.raises(ValidationError) as caught:
        _Settings.model_validate({"group": {"host": "h", "bogus": 1}})
    assert _errors(caught.value) == [("extra_forbidden", ("group", "bogus"))]
