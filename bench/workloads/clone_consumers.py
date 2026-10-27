"""Synthetic record shapes for application-facing clone regressions.

These fixtures preserve the small and large forms of question, media, retained
material and comparison records. They contain no application data or imports.
Pydantic is optional and required only when this workload set is selected.
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, JsonValue, PrivateAttr

from bench.profiles.clone_cpu import Workload


if TYPE_CHECKING:
    from collections.abc import Iterable


type Fingerprint = tuple[str, str | int | bool | bytes | tuple[Fingerprint, ...]]


class Question(BaseModel):
    """A small model whose optional arguments can contain large data trees."""

    kind: Literal["ask", "confirm"] = "ask"
    input_id: str = "question"
    question: str = "Choose a source."
    context: str = ""
    args: dict[str, JsonValue] = Field(default_factory=dict)
    choices: list[str] = Field(default_factory=list)
    blocking: bool = False
    _publication: dict[str, JsonValue] = PrivateAttr(default_factory=dict)
    _private_context: bool = PrivateAttr(default=False)


class PreparedImage(BaseModel):
    """Prepared bytes remain immutable while their metadata is copied."""

    data: bytes
    mime_type: str = "image/png"
    width: int = 1280
    height: int = 720
    label: str = "preview"


class Attachment(BaseModel):
    """A scalar-heavy model with nested small prepared-image records."""

    id: str = "attachment"
    data: bytes = b"source bytes"
    filename: str = "image.png"
    mime_type: str = "image/png"
    transcription: str = ""
    media_context: str = ""
    media_error: str = ""
    prepared_text: str | None = None
    prepared_images: list[PreparedImage] = Field(default_factory=list)
    prepared_pdf: bytes = b""
    prepared_pdf_pages: int = 0


class Material(BaseModel):
    """An independently owned retained source with ordinary nested data."""

    id: str
    title: str
    kind: str = "document"
    content: str = "Saved source content."
    data: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Change:
    """Immutable comparison rows still own mutable before/after data."""

    before: dict[str, JsonValue] | None
    after: dict[str, JsonValue] | None


@dataclass
class Record:
    """Positional conversion must expand repeated field values separately."""

    left: list[dict[str, JsonValue]]
    right: list[dict[str, JsonValue]]


class CopyHook:
    """A foreign protocol shares the caller's memo with its ordinary sibling."""

    def __init__(self, values: list[int]) -> None:
        """Retain the foreign value shared by the fixture's ordinary field."""
        self.values = values

    def __deepcopy__(self, memo: dict[int, object]) -> dict[str, list[int]]:
        """Use the actual caller memo rather than creating an isolated graph."""
        return {"copied": copy.deepcopy(self.values, memo)}


def _children(value: object) -> Iterable[object]:
    """Inspect only fixture-owned graph shapes, including all model state."""
    if isinstance(value, BaseModel):
        return (
            value.__dict__,
            value.__pydantic_extra__,
            value.__pydantic_private__,
            value.__pydantic_fields_set__,
        )
    if isinstance(value, dict):
        return tuple(child for pair in value.items() for child in pair)
    if isinstance(value, list | tuple | set):
        return tuple(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return tuple(getattr(value, field.name) for field in dataclasses.fields(value))
    if isinstance(value, CopyHook):
        return (value.values,)
    return ()


def _fingerprint(value: object, seen: dict[int, int]) -> Fingerprint:
    """Encode exact types, order and aliases without recursing through cycles."""
    if value is None:
        return ("none", ())
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is str:
        return ("str", value)
    if type(value) is bytes:
        return ("bytes", value)
    if type(value) is float:
        return ("float", value.hex())
    identity = id(value)
    if identity in seen:
        return ("ref", seen[identity])
    seen[identity] = len(seen)
    kind = type(value)
    if isinstance(value, BaseModel) or dataclasses.is_dataclass(value):
        name = f"{kind.__module__}.{kind.__qualname__}"
    elif kind in (dict, list, tuple, set):
        name = kind.__name__
    else:
        message = f"Unsupported cloned fixture value: {kind.__qualname__}"
        raise TypeError(message)
    children = sorted(value, key=repr) if isinstance(value, set) else _children(value)
    return (
        name,
        tuple(_fingerprint(child, seen) for child in children),
    )


def _mutable_ids(value: object) -> set[int]:
    found: set[int] = set()
    visited: set[int] = set()

    def visit(node: object) -> None:
        identity = id(node)
        if identity in visited:
            return
        visited.add(identity)
        if isinstance(node, BaseModel | dict | list | set) or (
            dataclasses.is_dataclass(node) and not isinstance(node, type)
        ):
            found.add(identity)
        for child in _children(node):
            visit(child)

    visit(value)
    return found


def _case(
    name: str, source: object, operation: Literal["deep", "asdict"] = "deep"
) -> Workload:
    expected: object
    if operation == "asdict":
        if not dataclasses.is_dataclass(source) or isinstance(source, type):
            message = "asdict workload requires a dataclass instance"
            raise TypeError(message)
        expected = dataclasses.asdict(source)
    else:
        expected = copy.deepcopy(source)
    expected_shape = _fingerprint(expected, {})
    source_mutables = _mutable_ids(source)

    def check(result: object, _reuse_tuples: bool) -> bool:
        return _fingerprint(result, {}) == expected_shape and not (
            _mutable_ids(result) & source_mutables
        )

    return Workload(name, source, check, operation)


def workloads() -> list[Workload]:
    """Keep scalar-heavy records beside the nested data they sometimes carry."""
    shared: list[JsonValue] = [{"id": 1, "values": [1, 2]}]
    question = Question()
    question.args["left"] = shared
    question.args["right"] = shared
    question._publication = {"source": "retained", "rows": shared}
    cycle: dict[str, object] = {"values": [1, 2]}
    cycle["cycle"] = cycle
    hooked = [1, 2]
    record_shared: list[dict[str, JsonValue]] = [{"id": 1, "values": [1, 2]}]
    record = Record(record_shared, record_shared)
    return [
        _case("consumer-question-tiny", Question()),
        _case(
            "consumer-question-nested-100",
            Question(
                args={
                    "rows": [
                        {"id": index, "values": [index, "row"]} for index in range(100)
                    ]
                }
            ),
        ),
        _case("consumer-question-private-alias", question),
        _case("consumer-attachment-empty", Attachment()),
        _case(
            "consumer-attachment-images-10",
            Attachment(
                prepared_images=[
                    PreparedImage(data=b"image bytes", label=str(index))
                    for index in range(10)
                ]
            ),
        ),
        _case(
            "consumer-materials-scalar-40",
            [Material(id=str(index), title="Saved document") for index in range(40)],
        ),
        _case(
            "consumer-materials-data-40",
            [
                Material(
                    id=str(index),
                    title="Saved document",
                    data={"rows": [{"value": row} for row in range(20)]},
                )
                for index in range(40)
            ],
        ),
        _case("consumer-change-slots-tiny", Change(None, {"values": [1, 2]})),
        _case(
            "consumer-change-slots-1000",
            tuple(
                Change(
                    {"id": index, "values": [index]},
                    {"id": index, "values": [index + 1]},
                )
                for index in range(1000)
            ),
        ),
        _case("consumer-dataclass-field-alias", record),
        _case("consumer-asdict-field-alias", record, "asdict"),
        _case("consumer-builtin-cycle", cycle),
        _case(
            "consumer-copy-hook-alias", {"values": hooked, "foreign": CopyHook(hooked)}
        ),
    ]
