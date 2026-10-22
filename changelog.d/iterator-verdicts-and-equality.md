Fix iterator contracts exposed by adversarial tests. `partition` freezes each
predicate result to a boolean once, preventing mutable or side-effecting truth
values from dropping or duplicating items across its two outputs.
`unique_everseen` now deduplicates equal hashable and unhashable keys across one
shared history (including sets/frozensets), preserves the first object, and
propagates TypeError raised by hashable-key comparisons instead of hiding it.
