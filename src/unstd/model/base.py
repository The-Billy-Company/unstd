"""The one strict Pydantic spine every wire shape subclasses.

A ``pydantic.BaseModel`` at its defaults is *lenient*: ``extra='ignore'`` silently
drops unknown keys, and a field with an alias can only be populated by that alias.
For a shape that crosses a trust boundary — an LLM tool ``args_schema``, an HTTP /
Connect body, a structured-extraction target — that leniency is a liability:
unknown keys should be *rejected* (so a malformed call surfaces instead of being
half-applied), and the JSON schema the model emits should say
``additionalProperties: false`` (so a constrained/JSON-mode decoder emits a valid
call first try). :class:`StrictModel` bakes that posture onto the model so a schema
author sees it from the class and every derived shape inherits it — pydantic merges
parent + child ``ConfigDict`` at each configuration boundary.

The config is deliberately **minimal and parity-safe**:

* ``extra='forbid'`` — reject unknown keys. Parity for tool ``args_schema`` (the
  tool-call chokepoint already forbids unknown LLM args); a real, intended
  tightening for any other constructor, surfaced by tests and fixed at the root.
* ``validate_by_name=True`` — a strict *superset*: a field with an alias
  (``Field(alias='from')``) may be populated by the alias **or** the Python name.
  ``validate_by_alias`` stays at its default ``True``, so no alias-keyed caller
  breaks. (2.11+ spelling; supersedes the deprecated ``populate_by_name``.)

Intentionally **not** set here — each silently changes values or adds a failure
mode and belongs to a later, separately-proven pass: ``str_strip_whitespace``
(mutates strings), ``use_enum_values`` (changes dump shape), ``validate_assignment``
(per-assign cost + new failure mode), and any serialization default (``model_dump``
by-alias — pydantic cannot default those via ``ConfigDict`` and overriding
``model_dump`` would change wire output).

Backend: pydantic has **no stdlib equivalent**, so this module
hard-requires the ``model`` extra (``pip install 'unstd[model]'``) and raises a
clear ImportError pointing at it rather than a bare ``ModuleNotFoundError`` — the
same posture ``unstd.serde.structs`` takes for msgspec.
"""

from __future__ import annotations


try:
    from pydantic import BaseModel, ConfigDict
except ImportError as exc:  # pydantic has no stdlib equivalent
    msg = "unstd.model requires the 'model' extra — install with: pip install 'unstd[model]'"
    raise ImportError(msg) from exc


__all__ = ["FrozenModel", "StrictModel"]


class StrictModel(BaseModel):
    """Base for wire shapes — strict by default, alias-tolerant.

    Subclass this instead of ``pydantic.BaseModel`` for tool ``args_schema``
    models, CRUD/wire bodies, and structured-extraction targets. Rejects unknown
    fields (``extra='forbid'``) and accepts either the field name or its alias on
    input (``validate_by_name=True``). The config merges through subclassing, so
    a house shape (``class ProjectBody(StrictModel)``) and everything derived from
    it stay strict without restating the config.
    """

    model_config = ConfigDict(extra="forbid", validate_by_name=True)


class FrozenModel(StrictModel):
    """Immutable :class:`StrictModel` — a hashable, once-built value object.

    ``frozen=True`` merges onto the strict posture, so instances reject unknown
    fields, accept name-or-alias input, refuse post-construction mutation, and gain
    a ``__hash__`` (usable as a dict key / set member). Reach for this over
    ``class Frozen(StrictModel, frozen=True)`` when a shape is a value object that
    should never mutate after validation.
    """

    model_config = ConfigDict(frozen=True)
