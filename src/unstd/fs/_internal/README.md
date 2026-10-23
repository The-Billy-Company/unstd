# Filesystem internals

`durability.py` owns directory synchronization and descriptor cleanup.
`walk.py` owns descriptor-anchored, portable and symlink-following traversal.
The public `unstd.fs` module passes its operating-system binding explicitly,
keeping fault injection and platform behavior consistent. Atomic-write
orchestration stays at the public module so its sync and cleanup hooks retain
their existing behavior. These modules are private implementation details.
