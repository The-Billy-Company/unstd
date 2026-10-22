# Filesystem regressions

Run `uv run pytest tests/suites/product/fs/regression -q` from the repository root.

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
