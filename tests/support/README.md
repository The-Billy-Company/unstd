# Shared support

`anchors.py` locates repository artifacts without depending on suite depth.
`hypothesis/` owns generation profiles. Add reusable fixtures, strategies,
assertion helpers, and reviewed artifacts here when a real test needs them.
Keep product expectations in the owning suite and test support machinery under
`suites/assurance/gates`.

Prefer real temporary files, subprocesses, and loopback peers when their behavior
is the contract. Introduce a fake only at a genuine external seam; a reused fake
needs behavior tests and real-dependency parity before its results count as
evidence. Never route production imports through test support.
