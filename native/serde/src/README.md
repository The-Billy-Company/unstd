# JSON kernel source

| File | Responsibility |
| --- | --- |
| [`adapter.c`](adapter.c) | Module initialization, retained state, encoding proofs, whitespace insertion and exported methods |
| [`encode.h`](encode.h) | Bind the Python `dumpb` function to guarded native dispatch |
| [`decode.h`](decode.h) | Select a parser, prove decoded values and bind Python `loads` dispatch |
| [`lines.h`](lines.h) | Split, trim and decode strict NDJSON batches |

Only `adapter.c` is compiled. The headers contain static implementation fragments
whose include order follows shared declarations; they are not public interfaces.

`encode`, `dumpb` and `line` prove exact builtin trees and call the retained
`orjson` builtin in one uninterrupted GIL span. Do not allocate, invoke Python or
release the GIL while validating: borrowed container references and the completed
proof must still describe the tree serialized. Unsupported values return
`NotImplemented`; `MAX_DEPTH`, `MAX_VALUES` and interpreter headroom bound work.
Whitespace insertion happens after serialization and skips quoted string spans.

`decode.h` uses `msgspec` for small documents, low native stack headroom and
array prefixes indicating floats. Larger documents may use `orjson`, but a
bounded result proof must exclude floats; otherwise the original document is
retried with `msgspec`. `lines.h` skips blank rows and uses the same decoder;
a refused or invalid row discards the batch for whole-input Python replay with
its line numbers and diagnostics. Allocation failures propagate.

`configure` retains backend functions and the Python module's actual globals;
a dictionary watcher invalidates cached configuration. `encode.h` and `decode.h`
install vectorcall bindings only on CPython 3.14, preserving Python function
objects. Code, defaults, configuration and tracing/monitoring changes select
ordinary dispatch. `is_bound` and `dispatch_ready` expose these states to Python.
Module teardown detaches watchers and restores owned vectorcall slots. Review
private stack, dictionary and monitoring layout gates before adding a Python
minor version; keep refusal distinct from allocation and callback errors.
