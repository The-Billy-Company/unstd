# Product contracts

Mirror `src/unstd/<module>` here, then name the evidence kind. Dedicated
`unit`, `parity`, `properties`, `security`, and later `regression`, `integration`,
or `stress` leaves remain siblings under the owning module. Do not create a
global property or regression bucket.

Existing mixed API files retain their assertions. New files should make the
evidence kind explicit. Add the leaf to `tests/tooling/inventory.toml`; every
test file must have exactly one active suite owner. Shared strategy construction
belongs under `tests/support`, while the module-specific law stays here.

See [the coverage plan](../../COVERAGE.md) before adding modules. No test stub
that always passes or skips counts as implementing a planned lane.
