"""The shared strict base for a service's env-validated configuration tree.

Where :class:`~unstd.model.base.StrictModel` is the spine for *wire* shapes,
:class:`StrictSettings` is the spine for the ``pydantic-settings`` ``BaseSettings``
tree a deployable uses to validate its environment at startup. It carries
the same alias-tolerance so a field may be populated by its Python name or its
env alias, and it fixes the one posture a settings root must get right:

* ``extra='ignore'`` — a settings root reads from the real process environment (+
  ``.env``), which is full of variables unrelated to *this* service. Forbidding
  extras there would crash the service on any stray env var, so the robust posture
  is to *ignore* unknown keys at the root. (This is the deliberate inverse of
  :class:`StrictModel`'s ``extra='forbid'`` — strictness for a config tree comes
  from typed, validated fields, not from rejecting the ambient environment. Nested
  group models that subclass ``StrictModel`` keep ``forbid``.)
* ``validate_by_name=True`` — alias-or-name input, matching ``StrictModel``.

Backend: ``pydantic-settings`` has no stdlib equivalent, so this
module hard-requires the ``model`` extra (``pip install 'unstd[model]'``) and
raises a clear ImportError pointing at it — the ``unstd.serde.structs`` posture.
"""

from __future__ import annotations


try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError as exc:  # pydantic-settings has no stdlib equivalent
    msg = "unstd.model.settings requires the 'model' extra — install with: pip install 'unstd[model]'"
    raise ImportError(msg) from exc


__all__ = ["StrictSettings"]


class StrictSettings(BaseSettings):
    """Base for a service's env-validated ``BaseSettings`` tree — alias-tolerant, env-safe at the root.

    Subclass this instead of ``pydantic_settings.BaseSettings`` for a service's
    root Settings model. Accepts either field name or alias on input
    (``validate_by_name=True``) and ignores env vars it does not declare
    (``extra='ignore'``) so an unrelated ambient variable never fails startup.
    Nested group models should subclass :class:`~unstd.model.base.StrictModel`
    (``extra='forbid'``) — the leniency is only correct at the env-reading root.
    """

    model_config = SettingsConfigDict(extra="ignore", validate_by_name=True)
