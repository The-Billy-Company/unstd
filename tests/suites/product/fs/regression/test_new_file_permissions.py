"""New atomic files stay owner-only even when the caller's umask allows sharing.

Every permission profile runs in a separate bounded interpreter so no test
changes the pytest process's umask. Scratch and published modes are observed
independently; rewrites retain the caller's already explicit permission policy.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")

_WORKER = r"""
import json
import os
from pathlib import Path
import stat
import sys
from unstd import fs

operation, mask_text, previous_text = sys.argv[1:]
path = Path('state')
if previous_text != 'new':
    path.write_bytes(b'previous data')
    path.chmod(int(previous_text))
os.umask(int(mask_text))
real_os = fs.os
scratch_modes = []
class Boundary:
    def __getattr__(self, name):
        return getattr(real_os, name)
    def open(self, path, flags, mode):
        descriptor = real_os.open(path, flags, mode)
        scratch_modes.append(stat.S_IMODE(real_os.fstat(descriptor).st_mode))
        return descriptor
    def umask(self, value):
        raise AssertionError('production changed the process-wide umask')
fs.os = Boundary()
try:
    if operation == 'write':
        fs.atomic_write(path, b'complete new data')
    else:
        with fs.atomic_writer(path, mode='wb') as writer:
            writer.write(b'complete new data')
finally:
    fs.os = real_os
published_mode = stat.S_IMODE(path.stat().st_mode)
# Preserve the observed mode before restoring owner-read permission solely to
# inspect complete data under masks that deliberately deny even the owner.
if not published_mode & 0o400:
    path.chmod(0o600)
print(json.dumps({
    'mode': published_mode,
    'scratch_modes': scratch_modes,
    'contents': path.read_bytes().decode(),
    'entries': sorted(entry.name for entry in Path.cwd().iterdir()),
}))
"""


def _permissions(tmp_path: Path, operation: str, mask: int, previous: int | str):
    result = run_python(
        _WORKER, [operation, str(mask), str(previous)], cwd=tmp_path, timeout=10
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["contents"] == "complete new data"
    assert evidence["entries"] == ["state"]
    assert len(evidence["scratch_modes"]) == 1
    return evidence


@pytest.mark.parametrize("operation", ["write", "writer"])
@pytest.mark.parametrize(
    "mask", [0o000, 0o001, 0o002, 0o022, 0o077, 0o200, 0o400, 0o777]
)
def test_new_atomic_file_never_defaults_to_group_or_world_access(
    tmp_path: Path, operation: str, mask: int
) -> None:
    evidence = _permissions(tmp_path, operation, mask, "new")
    expected = 0o600 & ~mask
    assert evidence["mode"] == expected
    assert evidence["scratch_modes"] == [expected]
    assert not evidence["mode"] & 0o077


@pytest.mark.parametrize("operation", ["write", "writer"])
@pytest.mark.parametrize("mask", [0o000, 0o022, 0o077])
@pytest.mark.parametrize("previous", [0o600, 0o640, 0o660, 0o664, 0o666])
def test_existing_permissions_remain_an_explicit_caller_policy(
    tmp_path: Path, operation: str, mask: int, previous: int
) -> None:
    evidence = _permissions(tmp_path, operation, mask, previous)
    assert evidence["mode"] == previous
    assert evidence["scratch_modes"] == [previous & ~mask]
