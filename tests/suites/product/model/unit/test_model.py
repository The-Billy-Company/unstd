"""Adversarial + parity tests for the strict Pydantic spine (``unstd.model``).

These pin the contract the whole repo now depends on: the config is exactly what
we claim, it is *inherited* through subclassing (including a multiple-inheritance
diamond), the alias superset accepts name-or-alias, ``FrozenModel`` is immutable +
hashable, and ``StrictSettings`` keeps the env-safe ``extra='ignore'`` posture that
is the deliberate inverse of ``StrictModel``.
"""

from __future__ import annotations

from pydantic import Field, ValidationError
import pytest

from unstd.model import FrozenModel, StrictModel, StrictSettings


# ── the contract itself ───────────────────────────────────────────────────────


def test_strict_model_config_is_exactly_the_declared_contract() -> None:
    """Test strict model config is exactly the declared contract."""
    assert StrictModel.model_config.get("extra") == "forbid"
    assert StrictModel.model_config.get("validate_by_name") is True


def test_extra_key_is_forbidden() -> None:
    """Test extra key is forbidden."""

    class Sample(StrictModel):
        name: str

    assert Sample(name="ok").name == "ok"
    with pytest.raises(ValidationError) as caught:
        Sample.model_validate({"name": "ok", "unexpected": 1})
    assert [(e["type"], e["loc"]) for e in caught.value.errors()] == [
        ("extra_forbidden", ("unexpected",))
    ]


def test_validate_by_name_is_a_superset_alias_or_name_both_populate() -> None:
    """Test validate by name is a superset alias or name both populate."""

    class Aliased(StrictModel):
        frm: str = Field(alias="from")

    assert Aliased.model_validate({"from": "x"}).frm == "x"  # alias still works…
    assert Aliased.model_validate({"frm": "x"}).frm == "x"  # …and the Python name too
    assert Aliased(frm="x").model_dump() == {"frm": "x"}
    assert Aliased(frm="x").model_dump(by_alias=True) == {"from": "x"}


# ── inheritance: config merges through house shapes + the MI diamond ───────────


def test_config_inherited_through_subclassing_and_the_diamond() -> None:
    """Test config inherited through subclassing and the diamond."""

    class Body(StrictModel):
        name: str

    class _WithID(StrictModel):
        id: int

    class UpdateInput(Body, _WithID):
        pass

    assert Body.model_config.get("extra") == "forbid"
    assert UpdateInput.model_config.get("extra") == "forbid"
    assert UpdateInput.model_config.get("validate_by_name") is True
    assert UpdateInput(id=7, name="Acme").model_dump() == {"id": 7, "name": "Acme"}
    with pytest.raises(ValidationError) as caught:
        UpdateInput.model_validate({"id": 7, "name": "Acme", "bogus": 1})
    assert [(e["type"], e["loc"]) for e in caught.value.errors()] == [
        ("extra_forbidden", ("bogus",))
    ]


# ── FrozenModel: strict + immutable + hashable ─────────────────────────────────


def test_frozen_model_is_strict_immutable_and_hashable() -> None:
    """Test frozen model is strict immutable and hashable."""

    class Point(FrozenModel):
        x: int
        y: int

    assert Point.model_config.get("extra") == "forbid"
    assert Point.model_config.get("frozen") is True

    p = Point(x=1, y=2)
    assert {p} == {Point(x=1, y=2)}  # hashable, value-equal
    assert hash(p) == hash(Point(x=1, y=2))
    assert p != Point(x=1, y=3)
    with pytest.raises(ValidationError) as frozen:
        p.x = 9  # frozen — no post-construction mutation
    assert [e["type"] for e in frozen.value.errors()] == ["frozen_instance"]
    assert p.x == 1
    with pytest.raises(ValidationError) as extra:
        Point.model_validate({"x": 1, "y": 2, "z": 3})  # still forbids extra
    assert [(e["type"], e["loc"]) for e in extra.value.errors()] == [
        ("extra_forbidden", ("z",))
    ]


# ── StrictSettings: the deliberate env-safe inverse ────────────────────────────


def test_strict_settings_ignores_extra_at_the_env_root() -> None:
    # The inverse of StrictModel on purpose: a settings root reads the ambient
    # environment, so forbidding extras would crash on any unrelated env var.
    """Test strict settings ignores extra at the env root."""
    assert StrictSettings.model_config.get("extra") == "ignore"
    assert StrictSettings.model_config.get("validate_by_name") is True

    class Cfg(StrictSettings):
        port: int = 8080

    assert Cfg(port=9000).port == 9000
    ignored = Cfg.model_validate({"port": 9000, "unrelated_ambient_var": "x"})
    assert ignored.model_dump() == {"port": 9000}
    assert ignored.model_extra is None
