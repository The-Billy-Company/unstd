# `unstd.model` — the strict Pydantic spine

One shared base for every Pydantic wire shape across every Python deployable in
a program. A raw `pydantic.BaseModel` runs _lenient_ defaults (`extra='ignore'`,
alias-only population); `unstd.model` turns the strictness contract into a property
of the model itself, so a schema author sees the posture from the class and every
derived shape inherits it (pydantic merges parent + child `ConfigDict`).

```python
from unstd.model import StrictModel, FrozenModel, StrictSettings


class CreateProjectInput(StrictModel):  # extra='forbid' + validate_by_name
    name: str
```

## The family (three shapes, one posture)

| Class            | Config                                                     | For                                                                         |
| ---------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------- |
| `StrictModel`    | `extra="forbid"`, `validate_by_name=True`                  | tool `args_schema`, wire/CRUD bodies, extraction targets — the default base |
| `FrozenModel`    | `StrictModel` + `frozen=True`                              | immutable, hashable value objects                                           |
| `StrictSettings` | `BaseSettings` + `extra="ignore"`, `validate_by_name=True` | a service's env-validated config tree                                       |

## The contract

| Config             | Value                      | Why it's parity-safe                                                                                                                                                                                                                                                                                                 |
| ------------------ | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extra`            | `"forbid"` (models)        | The AI tool-call chokepoint already forbids unknown args on LLM input (unless a schema declares `extra="allow"`). Declarative parity for `args_schema` models; tightens the emitted JSON schema to `additionalProperties: false` (better constrained decoding). A real, tested tightening for any other constructor. |
| `extra`            | `"ignore"` (settings root) | A `BaseSettings` root reads the real process env + `.env`, full of unrelated vars — `forbid` would crash startup on a stray one. Strictness for config comes from typed fields, not from rejecting the ambient environment. Nested group models stay on `StrictModel` (`forbid`).                                    |
| `validate_by_name` | `True`                     | Strict superset — an aliased field may be populated by alias **or** Python name. `validate_by_alias` stays `True` (default), so no alias-keyed caller breaks. Supersedes the deprecated `populate_by_name`.                                                                                                          |

**Deliberately not set** (each mutates values or adds a failure mode — later,
separately-proven passes own them): `str_strip_whitespace`, `use_enum_values`,
`validate_assignment`, and any serialization default.

## Backend

Unlike `serde.jsonx`/`b64`/`ndjson`, Pydantic has **no stdlib equivalent**, so
`unstd.model` hard-requires the `model` extra (`pip install 'unstd[model]'`,
providing `pydantic` + `pydantic-settings`). Imported without it, it raises a clear
ImportError pointing at the extra — the same posture `unstd.serde.structs` takes
for msgspec.

## Migrating a model

Swap the base — nothing else changes:

```diff
-class Foo(BaseModel):
+class Foo(StrictModel):
```

Keep `Field`, `field_validator`, `model_validator`, etc. imported from `pydantic`
as-is. The tightening from an enum-in-a-`description` string to `Literal`/`StrEnum`
is a **separate** behavior-changing step — do it deliberately, per field, not as
part of a base swap.

## Pydantic vs. msgspec — which to use

- **`unstd.model` (pydantic) at every trust boundary** — LLM tool args, HTTP /
  Connect bodies, config, structured extraction. Anything validated, aliased, or
  that must emit a JSON schema.
- **`unstd.serde.structs` (msgspec) for the hot internal machine-to-machine seam**
  — a fixed-schema payload where you own both ends and want the fastest
  validate-on-decode (cache blobs, internal envelopes). See the
  [`serde` README](../serde/README.md).

## Enforcement

The `py-strict-model` ratchet (`quality/discipline/lint/ratchets/py/strict-model/`)
keeps the migration permanent: a new class whose direct base is raw
`pydantic.BaseModel` in a covered path fails the gate; existing files are
grandfathered at a baseline that may only shrink.
