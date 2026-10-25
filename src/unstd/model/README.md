---
doc_radar:
  sentinels:
    - file: src/unstd/model/base.py
      contains: ['class StrictModel(BaseModel):', 'class FrozenModel(StrictModel):']
    - file: src/unstd/model/settings.py
      contains: ['class StrictSettings(BaseSettings):']
---

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

## Source ownership

This flat leaf owns one inherited configuration policy across models and settings.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public `StrictModel`, `FrozenModel`, and `StrictSettings` exports |
| [`base.py`](base.py) | Required Pydantic import and strict/frozen model configurations |
| [`settings.py`](settings.py) | Required settings backend and environment-settings configuration |

The [validation guide](#validation) gives the current product-suite run path.

## The family (three shapes, one posture)

| Class            | Config                                                     | For                                                                         |
| ---------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------- |
| `StrictModel`    | `extra="forbid"`, `validate_by_name=True`                  | tool `args_schema`, wire/CRUD bodies, extraction targets — the default base |
| `FrozenModel`    | `StrictModel` + `frozen=True`                              | prevents field assignment; hashable when all field values are hashable      |
| `StrictSettings` | `BaseSettings` + `extra="ignore"`, `validate_by_name=True` | a service's env-validated config tree                                       |

## The contract

| Config             | Value                      | Why it's parity-safe                                                                                                                                                                                                                                                                                                 |
| ------------------ | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extra`            | `"forbid"` (models)        | Rejects unknown constructor fields and emits `additionalProperties: false` in JSON Schema. Child models can override this configuration. |
| `extra`            | `"ignore"` (settings root) | Ignores extra constructor fields and extra keys from a configured dotenv source. Unrelated process environment variables are already filtered by the settings source. Dotenv loading is opt-in through `env_file` or `_env_file`; this base does not configure a `.env` file. Nested group models can use `StrictModel` to reject extras. |
| `validate_by_name` | `True`                     | Strict superset — an aliased field may be populated by alias **or** Python name. `validate_by_alias` stays `True` (default), so no alias-keyed caller breaks. Supersedes the deprecated `populate_by_name`.                                                                                                          |

**Deliberately not set** (each mutates values or adds a failure mode — later,
separately-proven passes own them): `str_strip_whitespace`, `use_enum_values`,
`validate_assignment`, and any serialization default.

These bases do not set Pydantic's `strict=True`: ordinary field coercion still
applies. Frozen models also retain any mutable objects stored inside fields.

## Backend

Unlike `serde.jsonx`/`b64`/`ndjson`, Pydantic has **no stdlib equivalent**, so
`unstd.model` hard-requires the `model` extra (`pip install 'unstd[model]'`,
providing `pydantic` + `pydantic-settings`). Imported without it, it raises a clear
ImportError pointing at the extra — the same posture `unstd.serde.structs` takes
for msgspec.

## Migrating a model

Swap the base, then check callers for the changed unknown-field and alias/name
population rules:

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

## Validation

[`tests/suites/product/model/unit/`](../../../tests/suites/product/model/unit/README.md)
checks field-name and alias population, unknown-field handling, frozen models,
emitted schemas, and missing-extra errors.
[`properties/`](../../../tests/suites/product/model/properties/README.md) compares
generated layouts with explicitly configured Pydantic twins, and
[`integration/`](../../../tests/suites/product/model/integration/README.md) checks
environment and dotenv precedence in isolated workers. Run from the repository root with
`uv run --no-sync pytest tests/suites/product/model -q`; install the `model`
extra to exercise these contracts.
