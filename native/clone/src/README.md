# Clone adapter

[`adapter.c`](adapter.c) contains the module state, admission cache, traversal
and reconstruction code for `unstd.clone._clone_native`.

| Entry point | Contract |
| --- | --- |
| `clone` | Prove and copy an exact builtin tree; unsupported values and cycles return `NotImplemented`. |
| `clone_checked` | The same tree semantics, but a proved cycle raises `RecursionError`; `asdict` copies repeated structural positions separately. |
| `clone_graph` | Validate while copying and preserve aliases in a pointer-key table; optionally consult and extend the Python memo. A detected cycle raises `RecursionError`. |
| `clone_walk_graph` | Walk mixed builtin and admitted dataclass graphs using the actual Python memo; select class handlers at each pinned source node and delegate foreign nodes in place. |
| `clone_walk_canonical_graph` | The canonical Python walk may also handle exact naive/UTC datetime and time leaves natively, preserving its memo, timezone and recursion policy. The original custom-walker entry point keeps delegating those leaves. |
| `clone_record_graph` | Walk a specified admitted record class with the same shared memo and callback rules. |
| `clone_records` | Copy trees containing an admitted record class; after copying starts, delegate foreign nodes to the supplied Python walker in place. |
| `clone_dataclass` | Recheck an admitted layout, memoize one safe empty twin, then populate its state through the actual shared-memo continuation. The optional `reuse_memo=False` marks an already selected and pinned Python handler. |

Builtin traversal accepts exact `dict` with exact string keys, `list`, `tuple`,
and supported immutable atoms. Its 128-level and 65,536-container budgets also
respect interpreter recursion headroom; atomic leaves do not spend the
container budget. Refusal returns `NotImplemented`; real allocation/callback
errors propagate.

The private graph table owns source/result references, admits only completed
containers, and exports entries to the caller's memo only after success. Export
scratch objects stay hidden from GC observers until initialized and committed.
The mixed walker instead publishes initialized mutable destinations before
their children, so foreign hooks can inspect and mutate them through the memo.
Lists expose only their initialized prefix; tuples enter the memo after their
children finish. A tuple completed through a mutable back-reference is reused.

Native record layout admission uses a bounded weak type/version cache in both
single-record and general graph copying; class changes invalidate it. Python
admission still runs for each class in each mixed walk. Successful graph copies
populate the layout cache after reconstruction completes. Mixed walks keep a
bounded per-call cache of owned class/handler
recipes, selected after the current source is pinned. Destination state updates
are captured before descendant hooks, and an allocated twin is finished rather
than retried. The mixed continuation preserves Python recursion headroom on
the validated CPython 3.12–3.14 ABI; other interpreters use the Python walker.

The canonical temporal shortcut starts only with a fresh memo. It checks the
Python walker, temporal class bindings and copy registries before use. Foreign
continuations disable it for the remaining graph; admission may retain it only
after checking that memo keys and source pins remain ordinary builtin values.
Older extensions retain the existing `clone_walk_graph` import and shared-memo
calling convention. The Python temporal policy remains authoritative.

Keep recursive items owned across allocations: GC/finalizers can mutate live
sources even with the GIL held. Lists are reread live, dictionary cursors check
mutation, and incomplete presized sequences stay untracked until safe to expose.
Scalar prefixes fill without allocations before recursive work. An initial
adjacent run in a private builtin graph reuses its completed child directly;
the memo owns both source and result across mutations. The run stops at the
original allocation or current source length, leaving growth to the live loop.
Caller memos and tree/foreign-node walks bypass this shortcut.
The [Python walker](../../../src/unstd/clone/structural.py) owns protocol
selection and whole-graph cycle fallback.
