# Shared test support

Reusable mechanisms live here; executable product and harness assertions stay in
[suites](../suites/README.md). Helpers operate real temporary files, subprocesses,
loopback peers, or public fault seams so tests can observe the boundary they claim.

Helpers are grouped by purpose; the root contains only this guide and the package
initializer. Import the specific module that owns the mechanism.

| Package | Responsibility |
| --- | --- |
| [`environment/`](environment/) | Repository paths (`anchors.py`), optional/required backend admission (`backends.py`), and owned Python subprocesses (`process.py`) |
| [hypothesis/](hypothesis/README.md) | Shared generation profiles loaded at collection |
| [`parsers/`](parsers/) | Bounded hostile parser/regex execution through `cases.py` and its isolated `worker.py` |
| [`security/`](security/) | Independent certificate fixtures and bounded loopback TLS peers in `tls.py` |
| [`packaging/`](packaging/) | Frozen source snapshots, offline artifact builds, pristine installed consumers, and probe definitions in `artifacts.py` |
| [`chaos/`](chaos/) | Versioned schedules and bounded journals (`journal.py`), plus filesystem/process, stream, clock, and entropy fault experiments |
| [`concurrency/`](concurrency/) | Gated filesystem, UUID, and shared-state histories with independent observations |
| [`resources/`](resources/) | Measured resource workers (`runner.py`), streaming/cache/OS probes, and deliberate failure controls |

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
