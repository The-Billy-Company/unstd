# Resource-measurement support

[`runner.py`](runner.py) contains killable workers, bounded output capture,
measurement, and cleanup. [`streams.py`](streams.py), [`parsers.py`](parsers.py),
and [`state.py`](state.py) provide streaming, JSON-depth, cache, and OS-resource
probes. [`controls.py`](controls.py) supplies deliberate failures proving that
containment and accounting reject bad runs.

The [resource suite](../../suites/assurance/adversarial/resources/README.md)
owns finite budgets and assertions. [Harness gates](../../suites/assurance/gates/resources/README.md)
verify the measuring instrument. These probes measure bounded resource behavior;
performance timing belongs in its separate lane.
