Large JSON arrays and objects no longer hit the native decoder's 65,536-value
proof limit and discard a complete parse. Validation work is bounded by source
length instead: every newly parsed value requires at least one source unit.
The existing depth cap, precise-number fallback, stdlib hooks, recursion limits
and diagnostic behavior remain in place.
