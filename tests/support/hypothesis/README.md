# unstd properties

These tests exercise `unstd` with bounded, contract-valid generated values.
Expectations come from independent authorities rather than a second call to the
implementation:

- Python's `json` defines exact encoded text, accepted input encodings, decoded
  types and values, and error classes/messages/source locations. Generated cases
  include surrogate code points, non-finite floats, arbitrary-precision integers,
  tuples, non-string keys, formatting options, malformed text, and invalid bytes.
  Bounds keep execution practical; they do not narrow the public JSON contract.
- Python's `struct` defines binary bytes, offsets, sizes, and truncation errors.
- Python's `base64` defines encoded bytes, validation and exception diagnostics.
  URL convenience decoding preserves stdlib-accepted inputs and additionally
  repairs omitted padding after ignored bytes are removed. Generated transport
  mutations retain interior padding so lenient termination is tested as well.
- Python's `tomllib` defines TOML read semantics and validates optional-writer output.
- Python's `copy.deepcopy` defines clone fidelity; post-mutation source snapshots
  prove reference isolation.

Properties live beside the module they exercise under
`tests/suites/product/<module>/properties`. Run all generated cases, including
those retained in mixed API files, with `uv run --no-sync pytest -q -m properties`
from the package root.

The shared profiles mirror Billy: `dev` (25 examples, local default), `default`
(100), deterministic `ci` (300, selected when `CI` is set), `campaign` (2500),
and `replay` (explicit examples and database reuse only). Override the selection
with `HYPOTHESIS_PROFILE`. All profiles print reproduction blobs and disable
wall-clock deadlines; generation health checks stay enabled. Keep depth out of
individual tests so campaign runs cannot be silently capped. `replay` is a
reproduction aid, not the generative CI gate.

The JSON and Base64 properties compare against the running interpreter, so version-specific
stdlib behavior remains authoritative. CI runs them on Python 3.12, 3.13, and
3.14 with optional backends, and on the real base installation without extras.
The same generated cases must pass in both configurations; backend limitations
are not excluded from the parity domain.
