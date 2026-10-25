# Clone kernel source

[`_clone_native.c`](_clone_native.c) builds `unstd.clone._clone_native`.
Builtin paths accept exact `dict` (string keys), `list`, `tuple`, and supported
immutable atoms; refusal returns `NotImplemented` to the Python walker.

- `clone_graph` validates while copying and preserves aliases in a pointer-key
  table holding source and result references. Only completed containers enter
  it; state-boundary calls consult the Python memo and export entries on success.
  Refusal discards staged entries. Detected cycles raise `RecursionError`, which
  `deep` catches to restart through whole-graph `deepcopy`.
- `clone_checked` proves eligibility before copying and reports proved cycles.
  Its tree semantics let `asdict` copy repeated structural positions separately.
- `clone_dataclass` rechecks the Python-admitted layout, memoizes a safe empty
  twin before state callbacks, and captures the destination update before
  descending. After allocation it finishes that twin rather than retrying.
  Admission uses a bounded weak type/version cache, invalidated by class changes.

Traversal allows at most 128 container levels and 65,536 container visits,
further constrained by interpreter recursion headroom; atoms do not spend the
container budget. Allocation may run GC/finalizers: recursive items stay owned,
lists are reread live, dictionary cursors check mutation, and incomplete
presized sequences stay untracked until safe to expose.
