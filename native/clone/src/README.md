# Clone adapter

[`adapter.c`](adapter.c) contains the module state, admission cache, traversal
and reconstruction code for `unstd.clone._clone_native`.

| Entry point | Contract |
| --- | --- |
| `clone` | Prove and copy an exact builtin tree; unsupported values and cycles return `NotImplemented`. |
| `clone_checked` | The same tree semantics, but a proved cycle raises `RecursionError`; `asdict` copies repeated structural positions separately. |
| `clone_graph` | Validate while copying and preserve aliases in a pointer-key table; optionally consult and extend the Python memo. A detected cycle raises `RecursionError`. |
| `clone_records` | Copy trees containing an admitted record class; after copying starts, delegate foreign nodes to the supplied Python walker in place. |
| `clone_dataclass` | Recheck an admitted layout, memoize one safe empty twin, then populate its state through the shared memo and walker. |

Builtin traversal accepts exact `dict` with exact string keys, `list`, `tuple`,
and supported immutable atoms. Its 128-level and 65,536-container budgets also
respect interpreter recursion headroom; atomic leaves do not spend the
container budget. Refusal returns `NotImplemented`; real allocation/callback
errors propagate.

The graph table owns source/result references, admits only completed containers,
and exports entries to the caller's memo only after success. Dataclass admission
uses a bounded weak type/version cache; class changes invalidate it. Destination
state updates are captured before descendant hooks, and an allocated twin is
finished rather than retried.

Keep recursive items owned across allocations: GC/finalizers can mutate live
sources even with the GIL held. Lists are reread live, dictionary cursors check
mutation, and incomplete presized sequences stay untracked until safe to expose.
The [Python walker](../../../src/unstd/clone/structural.py) owns protocol
selection and whole-graph cycle fallback.
