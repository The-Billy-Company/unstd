"""Real Linux filesystem qualification of the unshipped native commit proof."""

# ruff: noqa: PT009, PT027
# This standalone proof deliberately needs only stdlib unittest, not pytest.

from __future__ import annotations

import argparse
import errno
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
from typing import Any, cast, override
import unittest


_NATIVE: Any = None
_EXTENSION = Path()
_OBSERVER: Path | None = None
_CALL = """
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import _fs_experiment as native
path = pathlib.Path(sys.argv[2])
payload = b'next committed bytes\\x00\\xff' * 11
try:
    native.atomic_write(path, payload, durable=json.loads(sys.argv[3]))
except OSError as error:
    print(json.dumps({'errno': error.errno, 'notes': getattr(error, '__notes__', [])}))
else:
    print(json.dumps({'ok': True}))
"""
_PAYLOAD = b"next committed bytes\x00\xff" * 11


class CommitTests(unittest.TestCase):
    """Content, mode, lifetime, commit-boundary and syscall obligations."""

    @override
    def setUp(self) -> None:
        """Own every touched path inside a private temporary directory."""
        self.temporary = tempfile.TemporaryDirectory(prefix="unstd-fs-proof-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def child(self, path: Path, durable: bool | str, **settings: str) -> dict[str, Any]:
        """Call the native extension through the real ELF syscall seam."""
        self.assertIsNotNone(_OBSERVER, "build and pass the test observer")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("FS_PROOF_") and key != "LD_PRELOAD"
        }
        environment.update(settings)
        environment["LD_PRELOAD"] = str(_OBSERVER)
        environment["FS_PROOF_LOG"] = str(self.root / "syscalls.log")
        result = subprocess.run(  # noqa: S603 — fixed local child and test extension
            [
                sys.executable,
                "-c",
                _CALL,
                str(_EXTENSION),
                str(path),
                json.dumps(durable),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return cast("dict[str, Any]", json.loads(result.stdout))

    def events(self) -> list[str]:
        """Recorded syscall events from one isolated child."""
        return (self.root / "syscalls.log").read_text().splitlines()

    def test_binary_roundtrip_nested_parents_and_permissions(self) -> None:
        """New files are private and explicit existing sharing is retained."""
        path = self.root / "nested" / "雪" / "résumé.bin"
        for payload in (b"", bytes(range(256)) * 4, b"x" * (4 << 20)):
            self.assertIsNone(_NATIVE.atomic_write(path, payload))
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        path.chmod(0o640)
        _NATIVE.atomic_write(path, b"shared by policy", durable="strict")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(path.read_bytes(), b"shared by policy")
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_long_basename_uses_the_public_scratch_name_budget(self) -> None:
        """The scratch suffix adds 14 bytes, matching the Python implementation."""
        maximum = os.pathconf(self.root, "PC_NAME_MAX")
        path = self.root / ("n" * (maximum - 14))
        _NATIVE.atomic_write(path, b"complete")
        self.assertEqual(path.read_bytes(), b"complete")
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_failure_to_replace_a_directory_discards_the_scratch(self) -> None:
        """A failed rename preserves the old directory and leaves no scratch."""
        path = self.root / "directory"
        path.mkdir()
        with self.assertRaises(IsADirectoryError):
            _NATIVE.atomic_write(path, b"not committed")
        self.assertTrue(path.is_dir())
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_invalid_arguments_have_no_filesystem_effect(self) -> None:
        """Reject unadmitted payloads, durability and lexical names before I/O."""
        for path, payload, durable in (
            (self.root / "bad", "text requires encoding", False),
            (self.root / "bad", bytearray(b"mutable"), False),
            (self.root / "bad", b"bytes", "unknown"),
            ("", b"bytes", False),
            (str(self.root) + "/", b"bytes", False),
            (str(self.root / "nul") + "\x00", b"bytes", False),
        ):
            with (
                self.subTest(path=path, durable=durable),
                self.assertRaises((TypeError, ValueError)),
            ):
                _NATIVE.atomic_write(path, payload, durable=durable)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_descriptor_counts_remain_bounded_after_success_and_errors(self) -> None:
        """No file or directory handle survives success or failed rename."""
        path = self.root / "file"
        directory = self.root / "directory"
        directory.mkdir()
        before = len(list(Path("/proc/self/fd").iterdir()))
        for _ in range(8):
            _NATIVE.atomic_write(path, b"complete", durable="strict")
            with self.assertRaises(IsADirectoryError):
                _NATIVE.atomic_write(directory, b"not committed")
        self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), before)
        self.assertEqual(set(self.root.iterdir()), {path, directory})

    def test_real_readers_observe_only_complete_versions(self) -> None:
        """Concurrent readers cannot observe partial payloads or absent files."""
        path = self.root / "file"
        versions = (b"old" * 4096, b"new" * 8192)
        path.write_bytes(versions[0])
        done = threading.Event()
        failures: list[bytes | BaseException] = []
        reads: list[int] = []

        def reader() -> None:
            try:
                while not done.is_set():
                    payload = path.read_bytes()
                    reads.append(len(payload))
                    if payload not in versions:
                        failures.append(payload)
                        return
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for index in range(8):
                _NATIVE.atomic_write(path, versions[index % 2])
        finally:
            done.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(reads)
        self.assertEqual(failures, [])

    def test_default_real_file_sync_precedes_rename(self) -> None:
        """The default lane keeps file fsync and adds no directory fsync."""
        path = self.root / "file"
        self.assertEqual(self.child(path, False), {"ok": True})
        self.assertEqual(path.read_bytes(), _PAYLOAD)
        self.assertEqual(self.events(), ["file-sync", "rename"])

    def test_strict_sync_preflight_and_postcommit_order(self) -> None:
        """Strict writes acknowledge the parent both before and after rename."""
        path = self.root / "file"
        self.assertEqual(self.child(path, "strict"), {"ok": True})
        self.assertEqual(
            self.events(), ["directory-sync", "file-sync", "rename", "directory-sync"]
        )

    def test_strict_new_parent_entries_are_synced_in_order(self) -> None:
        """Each new parent entry is synced before committing the file."""
        path = self.root / "new" / "nested" / "file"
        self.assertEqual(self.child(path, "strict"), {"ok": True})
        self.assertEqual(
            self.events(),
            ["directory-sync"] * 3 + ["file-sync", "rename", "directory-sync"],
        )

    def test_failed_file_sync_preserves_prior_contents(self) -> None:
        """An injected kernel fsync error rolls back and discards the scratch."""
        path = self.root / "file"
        path.write_bytes(b"old committed version")
        result = self.child(path, False, FS_PROOF_FAIL_FILE_SYNC="1")
        self.assertEqual(result["errno"], errno.EIO)
        self.assertEqual(path.read_bytes(), b"old committed version")
        self.assertEqual(self.events(), ["file-sync-failed"])
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_failed_strict_preflight_keeps_prior_contents(self) -> None:
        """A failed preflight happens before any payload scratch is committed."""
        path = self.root / "file"
        path.write_bytes(b"old committed version")
        result = self.child(path, "strict", FS_PROOF_FAIL_DIR_SYNC_AT="1")
        self.assertEqual(result["errno"], errno.EIO)
        self.assertEqual(path.read_bytes(), b"old committed version")
        self.assertEqual(self.events(), ["directory-sync-failed"])
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_failed_postcommit_sync_identifies_the_committed_state(self) -> None:
        """Post-rename strict errors retain new bytes and a commit note."""
        path = self.root / "file"
        path.write_bytes(b"old committed version")
        result = self.child(path, "strict", FS_PROOF_FAIL_DIR_SYNC_AT="2")
        self.assertEqual(result["errno"], errno.EIO)
        self.assertEqual(path.read_bytes(), _PAYLOAD)
        self.assertTrue(any("already committed" in note for note in result["notes"]))
        self.assertEqual(
            self.events(),
            ["directory-sync", "file-sync", "rename", "directory-sync-failed"],
        )

    def test_best_effort_directory_failure_keeps_legacy_success(self) -> None:
        """Best-effort mode suppresses the directory sync error after commit."""
        path = self.root / "file"
        self.assertEqual(
            self.child(path, True, FS_PROOF_FAIL_DIR_SYNC_AT="1"), {"ok": True}
        )
        self.assertEqual(path.read_bytes(), _PAYLOAD)
        self.assertEqual(
            self.events(), ["file-sync", "rename", "directory-sync-failed"]
        )

    def test_interrupted_and_short_writes_publish_every_byte(self) -> None:
        """A first EINTR and 7-byte writes must not truncate the payload."""
        path = self.root / "file"
        self.assertEqual(
            self.child(path, False, FS_PROOF_SHORT_WRITE="1"), {"ok": True}
        )
        self.assertEqual(path.read_bytes(), _PAYLOAD)
        events = self.events()
        self.assertEqual(events[0], "write-interrupted")
        self.assertGreater(events.count("short-write"), 1)
        self.assertEqual(events[-2:], ["file-sync", "rename"])


def main() -> None:
    """Select the explicitly built proof and run its isolated contracts."""
    global _NATIVE, _EXTENSION, _OBSERVER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension-dir", type=Path, required=True)
    parser.add_argument("--observer", type=Path, required=True)
    args = parser.parse_args()
    _EXTENSION = args.extension_dir.resolve()
    _OBSERVER = args.observer.resolve()
    sys.path.insert(0, str(_EXTENSION))
    _NATIVE = importlib.import_module("_fs_experiment")
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(CommitTests)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
