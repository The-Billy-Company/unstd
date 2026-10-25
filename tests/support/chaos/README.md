# Fault-experiment support

[`journal.py`](journal.py) defines versioned deterministic schedules and bounded
JSON evidence. [`fs_process.py`](fs_process.py), [`streams.py`](streams.py), and
[`clock_entropy.py`](clock_entropy.py) run concrete filesystem/process, I/O,
clock, and entropy faults in owned workers.

Product recovery assertions belong in the
[chaos suite](../../suites/assurance/adversarial/chaos/README.md). Campaign
selection and replay live in [tooling](../../tooling/chaos/README.md); harness
containment and accounting tests live in the
[chaos gates](../../suites/assurance/gates/chaos/README.md).
