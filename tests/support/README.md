# Shared test support

Reusable mechanisms live here; executable product and harness assertions stay in
[suites](../suites/README.md). Helpers operate real temporary files, subprocesses,
loopback peers, or public fault seams so tests can observe the boundary they claim.

| Files | Responsibility |
| --- | --- |
| `anchors.py`, `backends.py` | Repository paths and optional/required backend admission |
| [hypothesis](hypothesis/README.md) | Shared generation profiles loaded at collection |
| `process.py`, `parser_cases.py`, `parser_worker.py` | Owned Python subprocesses and bounded hostile parser/regex execution |
| `tls.py` | Independent certificate fixtures and bounded loopback TLS peers |
| `packaging.py` | Frozen source snapshots, offline artifact builds, pristine installed consumers, and probe definitions |
| `chaos.py`, `chaos_*.py` | Versioned schedules, bounded journals, and filesystem/process, stream, clock, and entropy fault experiments |
| `concurrency_*.py` | Gated filesystem, UUID, and shared-state histories with independent observations |
| `resources.py`, `resource_*.py` | Measured resource workers, streaming/cache/OS probes, and deliberate failure controls |

Add a helper when real tests need to share a mechanism. Keep product-specific
expected outcomes in the owning suite; test harness admission, containment, and
failure accounting under [assurance/gates](../suites/assurance/gates/README.md).
Process-global patches and hostile inputs belong in deadline-owned workers.
Production modules must not import test support.

Owned process-group cleanup retains permission failures. On macOS, `killpg` can
report `EPERM` for a group containing only zombies. The shared process helper
accepts that case only after a bounded OS query confirms that the group is absent
or every remaining member is an owned-UID zombie; live, foreign, or uncertain
membership still fails. Worker supervisors reap their direct child even
when group cleanup reports an error.
