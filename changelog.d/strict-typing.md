Strengthen the required Ruff and mypy gates for production and benchmark code.
Require complete annotations, validate native stubs and override declarations,
reject blanket suppressions, and check additional control-flow, awaitable,
pattern-match, bytes, and optional-value contracts. Replace global missing-import
suppression with development-only protobuf and sorted-container stubs and narrow
exceptions for CPython's private regex modules. Preserve subclass types from
audio writer context managers and retain optional-backend runtime behavior.
Check Python 3.12–3.14 types for Linux, macOS, and Windows in CI, and add
real-analyzer positive and negative regression controls.
