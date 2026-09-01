"""Pydantic wire-shape spine — one strict base every model subclasses.

Three shapes, one posture: reject unknown keys, accept name-or-alias input, and
inherit both through subclassing so a schema author declares strictness once.

- :class:`StrictModel` — ``extra='forbid'`` + ``validate_by_name=True``. The
  default base for tool ``args_schema`` models, wire/CRUD bodies, and
  structured-extraction targets.
- :class:`FrozenModel` — ``StrictModel`` + ``frozen=True``. An immutable, hashable
  value object.
- :class:`StrictSettings` — the ``BaseSettings`` sibling for a service's
  env-validated config tree (``extra='ignore'`` at the env-reading root +
  ``validate_by_name=True``).

Backend: pydantic has no stdlib equivalent, so the group hard-requires
the ``model`` extra (``pip install 'unstd[model]'``). Import the members directly::

    from unstd.model import StrictModel, FrozenModel, StrictSettings


    class CreateProjectInput(StrictModel):
        name: str
"""

from __future__ import annotations

from unstd.model.base import FrozenModel, StrictModel
from unstd.model.settings import StrictSettings


__all__ = ["FrozenModel", "StrictModel", "StrictSettings"]
