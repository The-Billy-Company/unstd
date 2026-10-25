`clone.deep` detects builtin cycles in the native traversal, avoiding the
repeated Python recursion that preceded whole-graph `deepcopy`.
The native copier fills lists and tuples directly, presizes dictionaries, and
budgets container visits rather than scalar leaves so large scalar arrays stay
native. Dataclass/model state and `clone.asdict` fields now reuse native copying
at their structural boundaries.

Copy hooks, mutable isolation and cyclic alias preservation retain their
existing contracts. Shared-reference preservation is described separately in
`clone-shared-identity.md`. `clone.deepcopy` remains the
stdlib re-export. Paired CPU and elapsed-time profiles for Python 3.13 and 3.14,
including collector-enabled runs, are recorded in
`bench/reports/clone/clone-cpu-results.md`; gains depend on the data shape.
