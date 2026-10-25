JSONX keeps wide builtin trees and two-space indented documents on its native
encoding paths, preserves ASCII Unicode escapes without serializing a tree
twice, and avoids reparsing large objects with early floating-point values.
Python 3.12 indented floats retain stdlib's exact numeric spellings. Other
formatting, callbacks, unsupported values and interpreter limits retain their
existing stdlib fallbacks. Speedups depend on the payload and Python version;
the focused JSONX throughput benchmark records both gains and unchanged cases.

Float-record encoding adds a bounded native writer with operation-local float
and string spelling caches. Strict canonical snapshots use owned native copies
when the original Python walker, builtins and observers permit acceleration;
the original identity encoder still runs. Partial snapshots remain private
across collection, shared branches remain independent, and unsupported trees
retain their Python validation and error behavior. The retained safety tests
cover live-source mutation, forced collection, metadata changes, private-output
discard and cache cleanup. Existing public Python benchmarks qualify these
changes; they do not establish a universal 100× improvement. Separately
qualified shared-branch cases exceeding 100× are recorded in the
[shared-branch evidence](../bench/reports/serde/jsonx-100x.md).
