Added a shared registry with correctness-checked latency workloads for every
public operation across all 18 packages, a committed per-function speed snapshot,
and immutable regression ceilings with recorded runtime/backend metadata.
Coverage gates reject newly exported APIs without workloads or frozen entries.
An opt-in performance suite and a revision-comparison workflow enforce speeds
on compatible workers while keeping ordinary correctness tests free of timing
assertions.
