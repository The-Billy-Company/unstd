# Filesystem regressions

Run `uv run pytest tests/suites/product/fs/regression -q` from the repository root.

`test_new_file_permissions.py` retains the CodeQL-exposed new-file group/world
access under permissive umasks. Bounded isolated interpreters cover both atomic
write APIs and umasks `000`, `001`, `002`, `022`, `077`, `200`, `400` and `777`,
observing owner-only scratch and published modes, including further owner-bit
restrictions imposed by the kernel. Existing destinations preserve their explicit permission policy,
including shared group access, even under a stricter current umask. The production
writer is forbidden from changing process umask during these probes.

`test_atomic_mode.py` retains the destructive read-mode failure: opening an
atomic writer with `mode="r"` and leaving its body empty committed an empty
scratch over an existing destination. Unsupported modes now raise before any
filesystem changes; the regression checks retained bytes and absent parent
creation as well as rejection of the unsupported modes.

`test_atomic_permissions.py` retains two permission failures. An observer that
opened the initially public scratch file could read newly written secrets even
after chmod tightened the mode. Replacing the scratch path with a symlink also
redirected chmod onto an unrelated file. Real descriptors, inode permissions
and retained destination bytes prove the fixes; these tests require POSIX modes.

`test_walk_symlink_races.py` retains the confirmed traversal escape: the original
walker checked a directory entry without following links, queued its path, then
later followed a replacement symlink through `scandir(path)`. Real fixture-owned
directory replacements at the next open boundary reproduce queued-directory,
ancestor, and root swaps. Independent expectations admit only files from the
original tree. A symlink root, including one with a trailing slash, must also be
skipped by default while the explicit following mode remains functional.

The five original cases failed against the original implementation and passed
after descriptor-relative no-follow traversal was added. POSIX capability guards
name the required primitives. Additional tests establish lazy acquisition,
non-inherited descriptors, cleanup on exhaustion/early close/caller exceptions,
setup-failure cleanup, and a handle bound determined by depth rather than sibling
count. Explicit fallback tests preserve normal portable traversal and prove
identity rechecks reject deterministic replacements; they do not claim the
portable fallback closes every namespace race or pin later opens of returned
Paths. Those limits are documented in the public filesystem README.

`test_walk_cleanup.py` injects nested scanner iteration and close failures.
Every acquired scanner and directory descriptor must close, including all
ancestors when one close fails. The same cases retain lazy acquisition,
Unicode filenames, relative output names, and successful later traversal.
