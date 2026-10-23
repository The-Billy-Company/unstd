"""Safe YAML parser sessions and comment-preserving emission policy."""

from __future__ import annotations

from collections.abc import Hashable
from typing import TYPE_CHECKING, override

from ruamel.yaml.constructor import (
    ConstructorError,
    RoundTripConstructor,
    SafeConstructor,
)
from ruamel.yaml.emitter import RoundTripEmitter
from ruamel.yaml.representer import RoundTripRepresenter
from ruamel.yaml.scalarfloat import ScalarFloat


if TYPE_CHECKING:
    from ruamel.yaml import YAML
    from ruamel.yaml.nodes import MappingNode, Node, ScalarNode


class _SafeConstructor(SafeConstructor):
    """Validate source keys before merging disables backend duplicate checks."""

    _merge_source = False

    @override
    def flatten_mapping(self, node: MappingNode) -> None:
        """Keep explicit duplicates distinct from permitted merge overrides."""
        if node.merge is None and (
            self._merge_source
            or any(key.tag == "tag:yaml.org,2002:merge" for key, _value in node.value)
        ):
            keys: dict[Hashable, None] = {}
            for key_node, _value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                if key_node.tag == "tag:yaml.org,2002:value":
                    key_node.tag = "tag:yaml.org,2002:str"
                key: object = self.construct_object(key_node, deep=True)
                if isinstance(key, list):
                    key = tuple(key)
                if not isinstance(key, Hashable):
                    context = "while constructing a mapping"
                    problem = "found unhashable key"
                    raise ConstructorError(
                        context,
                        node.start_mark,
                        problem,
                        key_node.start_mark,
                    )
                if self.check_mapping_key(node, key_node, keys, key, None):
                    keys[key] = None
        # Aliased sources can already have been flattened. Their inherited
        # keys must not be checked again as explicit source declarations.
        # Ordinary mappings retain the backend's checks and diagnostics;
        # its recursive flatten calls identify inline merge sources too.
        prior_source = self._merge_source
        self._merge_source = True
        try:
            super().flatten_mapping(node)
        finally:
            self._merge_source = prior_source
        # The backend copies every aliased source's flattened pairs, so
        # repeated merges of one anchor grow exponentially with nesting.
        # Keep each pair's first insertion and final overwrite: removing
        # either can change key order or precedence around other sources.
        if node.merge:
            merged = len(node.merge)
            positions: dict[tuple[Node, Node], tuple[int, int]] = {}
            for index, pair in enumerate(node.merge):
                first, _last = positions.get(pair, (index, index))
                positions[pair] = (first, index)
            node.merge = [
                pair
                for index, pair in enumerate(node.merge)
                if index in positions[pair]
            ]
            node.value = node.merge + node.value[merged:]


class _Constructor(RoundTripConstructor):
    """An editable constructor that refuses unresolved recursive aliases."""

    @override
    def construct_object(self, node: Node, deep: bool = False) -> object:
        """Refuse the backend's unresolved ``None`` placeholder before data loss."""
        if node not in self.constructed_objects and node in self.recursive_objects:
            raise ConstructorError(
                None,
                None,
                "recursive alias cannot be preserved by the editable loader",
                node.start_mark,
            )
        value: object = super().construct_object(node, deep=deep)
        return value


# add_constructor copies inherited tables on the subclass before updating.
_Constructor.add_constructor(None, SafeConstructor.construct_undefined)
# Explicit standard !!str nodes remain strings in our public value domain.
# The backend's default TaggedScalar wrapper is outside that domain;
# construct_scalar retains quotes and anchors without admitting unknown tags.
_Constructor.add_constructor(
    "tag:yaml.org,2002:str", RoundTripConstructor.construct_scalar
)


class _Emitter(RoundTripEmitter):
    """Escape NEL, which the backend reader folds outside double quotes."""

    @override
    def choose_scalar_style(self) -> str | None:
        style: str | None = super().choose_scalar_style()
        return '"' if "\x85" in self.event.value else style


class _Representer(RoundTripRepresenter):
    """Keep parsed float spellings only while they still denote the value."""

    @override
    def represent_scalar_float(self, data: ScalarFloat) -> ScalarNode:
        node: ScalarNode = super().represent_scalar_float(data)
        try:
            exact = float(node.value) == data
        except ValueError:  # .inf and .nan spellings are exact
            exact = True
        if exact:
            return node
        # The backend truncates exponent mantissas instead of rounding them.
        text = self.represent_float(float(data)).value
        return self.represent_scalar(node.tag, text, anchor=node.anchor)


_Representer.add_representer(ScalarFloat, _Representer.represent_scalar_float)


def create_engine(factory: type[YAML], *, editable: bool = False) -> YAML:
    """A private parser/emitter session with fixed safe semantics."""
    engine = factory(typ="rt" if editable else "safe", pure=True)
    engine.Constructor = _Constructor if editable else _SafeConstructor
    if editable:
        engine.Emitter = _Emitter
        engine.Representer = _Representer
        engine.preserve_quotes = True
    engine.allow_duplicate_keys = False
    engine.default_flow_style = False
    return engine
