NDJSON readers and writers accept a reusable `structs.Codec` through `codec=`.
Readers decode each row directly into its validated schema, preserving physical
line numbers and schema paths on errors. `on_error` reports each rejected record
once before the normal raise or explicit skip. Untyped streaming still works on
a base install, and typed processing reuses the existing serde extra.
