"""Keep qualified package imports bound to their original provider modules."""

from __future__ import annotations

from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from importlib.util import find_spec
import sys
from typing import TYPE_CHECKING, override


__all__ = ()


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType


class _PortLoader(Loader):
    """Replace an import scratch module without changing provider metadata."""

    def __init__(self, canonical: str) -> None:
        """Retain the original module name for this qualified import."""
        self._canonical = canonical

    @override
    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        """Let importlib initialize an alias scratch module, never the provider."""
        return None

    @override
    def exec_module(self, module: ModuleType) -> None:
        """Publish the canonical singleton after its ordinary import succeeds."""
        sys.modules[module.__name__] = import_module(self._canonical)


class _PortFinder(MetaPathFinder):
    """Intercept descendants only for package aliases explicitly requested."""

    def __init__(self) -> None:
        """Start with no provider packages registered or imported."""
        self.packages: dict[str, str] = {}

    @override
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        """Resolve an alias child through the provider's normal import path."""
        for alias, canonical in self.packages.copy().items():
            if fullname.startswith(alias + "."):
                original = canonical + fullname[len(alias) :]
                specification = find_spec(original)
                if specification is None:
                    # Falling through to PathFinder would execute provider code
                    # again under the alias and create different native types.
                    message = f"No module named {fullname!r}"
                    raise ModuleNotFoundError(message, name=fullname)
                return ModuleSpec(
                    fullname,
                    _PortLoader(original),
                    is_package=specification.submodule_search_locations is not None,
                )
        return None


_finder = _PortFinder()


def register_package(alias: str, provider: ModuleType) -> None:
    """Route future child imports lazily, retaining optional provider backends."""
    if not hasattr(provider, "__path__"):
        return
    _finder.packages[alias] = provider.__name__
    if _finder not in sys.meta_path:
        sys.meta_path.insert(0, _finder)
