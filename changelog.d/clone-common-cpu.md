Accelerate mixed builtin/dataclass graphs with a native continuation that uses
the same memo as Python copy hooks. Per-call class recipes reduce repeated
dispatch while keeping shared references, live-source iteration, construction
order and fallback protocols. Memo-visible mutable results remain tracked and
contain only initialized items, including during reentrant inspection or
mutation. Native recursion follows Python's frame headroom on CPython 3.12–3.14.

Preserve ordinary mapping contents and types when Pydantic creates a filtered
private-state view, and restore completed mutable-container memo entries after
descendant overrides. CPU measurements use public Python APIs, including wrapper
and output-destruction costs, against the finished shared-identity implementation.
