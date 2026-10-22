"""Downstream strict typing resolves the installed wheel, including rejection controls."""

from __future__ import annotations

import sys

from tests.support.packaging.artifacts import Consumer, checked_command


_POSITIVE = """from datetime import datetime
from pathlib import Path
from typing import Literal, assert_type
from unstd import clone, fs, proc, rand, rex
from unstd.ids import uid
from unstd.pack import binpack
from unstd.serde import b64, jsonx
from unstd.time import timeutil

assert_type(b64.encode(b'wire'), str)
assert_type(b64.decode('d2lyZQ=='), bytes)
assert_type(jsonx.dumps({'value': 1}), str)
assert_type(jsonx.dumpb({'value': 1}), bytes)
assert_type(jsonx.canonical_strict({'value': 1}), bytes)
assert_type(binpack.pack_array_portable('i4', [1, 2]), bytes)
assert_type(binpack.unpack_array_portable('i4', b''), list[float])
assert_type(rand.Generator(173).random(), float)
assert_type(rand.Generator(173).backend, Literal['stdlib', 'numpy'])
assert_type(rex.compile_linear('[a-z]+').requires_ascii, bool)
assert_type(uid.new(), str)
assert_type(uid.new_hex(), str)
assert_type(clone.deep([1, 2]), list[int])
assert_type(fs.atomic_write(Path('output'), 'value'), Path)
assert_type(fs.atomic_write(Path('output'), 'value', durable='strict'), Path)
assert_type(proc.capture(['program']), str)
assert_type(timeutil.parse_utc('2026-01-01T00:00:00Z'), datetime | None)
"""

_NEGATIVE = """from unstd.ids import uid
from unstd.serde import b64, jsonx
from unstd import clone, fs, rand

wrong_bytes: bytes = b64.encode(b'wire')
wrong_text: str = b64.decode('d2lyZQ==')
wrong_json: bytes = jsonx.dumps({'value': 1})
wrong_id: int = uid.new()
original: list[int] = [1, 2]
wrong_clone: list[str] = clone.deep(original)
fs.atomic_write('output', 'value', durable='invalid')
rand.Generator(173, backend='auto')
"""


def test_downstream_strict_mypy_consumes_installed_types_with_negative_controls(
    consumer: Consumer,
) -> None:
    config = consumer.root / "consumer-mypy.ini"
    config.write_text("[mypy]\n", encoding="utf-8")
    positive = consumer.root / "typed_consumer.py"
    positive.write_text(_POSITIVE, encoding="utf-8")
    negative = consumer.root / "invalid_consumer.py"
    negative.write_text(_NEGATIVE, encoding="utf-8")
    common = [
        sys.executable,
        "-I",
        "-m",
        "mypy",
        "--config-file",
        config,
        "--python-executable",
        consumer.python,
        "--no-incremental",
        "--strict",
        "--ignore-missing-imports",
        "--follow-imports=silent",
        "--no-error-summary",
        "--show-error-codes",
        "--no-pretty",
    ]
    result = checked_command([*common, positive], cwd=consumer.root)
    assert result.stdout == result.stderr == ""
    rejected = checked_command([*common, negative], cwd=consumer.root, expected_code=1)
    errors = [line for line in rejected.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 7, rejected.stdout
    assert all("[assignment]" in line for line in errors[:4]), rejected.stdout
    assert all("[arg-type]" in line for line in errors[4:]), rejected.stdout
    assert not rejected.stderr
