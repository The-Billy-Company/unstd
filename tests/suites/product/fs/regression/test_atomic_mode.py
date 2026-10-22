"""Unsupported atomic-writer modes must not discard an existing destination.

Previously ``mode='r'`` wrapped the empty scratch file as a readable stream and
an empty context body committed it over an existing file. The documented writer
modes must be validated before creating either a parent directory or a scratch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from unstd import fs


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "mode", ["r", "rb", "r+", "w+", "a", "ab", "x", "xb", "wt", ""]
)
def test_unsupported_mode_preserves_destination(tmp_path: Path, mode: str) -> None:
    dest = tmp_path / "credentials"
    dest.write_bytes(b"existing credentials")

    with (
        pytest.raises(ValueError, match="mode must be 'w' or 'wb'"),
        fs.atomic_writer(dest, mode=mode),
    ):
        pass

    assert dest.read_bytes() == b"existing credentials"
    assert list(tmp_path.iterdir()) == [dest]


def test_unsupported_mode_does_not_create_parent_directory(tmp_path: Path) -> None:
    dest = tmp_path / "missing" / "credentials"

    with (
        pytest.raises(ValueError, match="mode must be 'w' or 'wb'"),
        fs.atomic_writer(dest, mode="r"),
    ):
        pass

    assert list(tmp_path.iterdir()) == []
