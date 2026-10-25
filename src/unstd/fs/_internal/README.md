# Filesystem internals

`durability.py` owns directory synchronization and descriptor cleanup.
`walk.py` owns descriptor-anchored, portable and symlink-following traversal.
The descriptor walker caches directory-name prefixes, constructing `Path`
objects only for yielded files. Each traversal frame owns its directory
descriptor and scanner; cleanup closes both and unwinds every live ancestor
even if another close fails. Live handles remain bounded by traversal depth.
The public `unstd.fs` module passes its operating-system binding explicitly,
keeping fault injection and platform behavior consistent. Atomic-write
orchestration stays at the public module so its sync and cleanup hooks retain
their existing behavior. These modules are private implementation details.
