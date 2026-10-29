# Interpreter TLS security preflight

Run with the exact interpreter that will execute tests or the application:

```console
uv run --no-sync python -m tests.tooling.runtime
uv run --no-sync python -m tests.tooling.runtime --json
```

The command is stdlib-only and checks `ssl.OPENSSL_VERSION` and its numeric
fields. A dependency such as `cryptography` can carry a different OpenSSL;
upgrading that dependency does not repair stdlib TLS. A successful check admits
the interpreter against this reviewed advisory snapshot; it is not a live
vulnerability scan or a promise about future advisories.

The [29 September 2026 OpenSSL advisory](https://openssl-library.org/news/secadv/20260929.txt)
fixes CVE-2026-35189 in 3.0.23 (premium support), 3.4.8, 3.5.9, 3.6.5 and 4.0.3.
The check compares patches within each branch. It rejects vulnerable versions,
unreviewed branches/engines and prereleases. Distributor backports need review
of the distributor's advisory and exact build: the upstream version number
alone cannot establish that they are fixed. Do not add an unverified bypass.
Refresh the policy when upgrading to a new OpenSSL branch or reviewing a new
upstream advisory, and retain the preflight output with release evidence.

For development on CPython 3.15.2, use the project's pinned patched interpreter.
The package still supports Python 3.12 and later. Validate every deployed
interpreter separately, including container and wheel-test interpreters; a
Python patch number alone does not identify the linked TLS library.

This module is never imported by the package. It runs during provisioning,
development/release validation, or explicitly at application startup, with no
version checks in context creation, handshakes, record processing or other
crypto operations.

The separate `clone_memory` module builds an isolated native clone audit with
forced collection, allocation failures or ASan/UBSan. See the
[clone memory audit instructions](../../suites/product/clone/support/README.md).
It records compiler/source provenance and preserves instrumentation in child
processes without overwriting production extensions.

`proc_memory` builds and tests an isolated native process backend, keeping its
loader and source selection in fixture-owned children:

```console
python -m tests.tooling.runtime.proc_memory --sanitize --output /tmp/proc-memory
```

It records source/binary/bootstrap hashes, native dispatch proof, loader options,
test output and JUnit results in the selected directory. Omit `--sanitize` for
debug-allocator qualification. Sanitizer controllers force stdlib fallback
launches through fork to avoid uninstrumented CPython vfork failures; admitted
native proc launches still execute the C spawn kernel. Exact environment
isolation runs in ordinary installed tests, and address-space allocation limits
run without ASan's independent virtual reservations.

`json_identity_gc` builds an isolated JSONX native extension with ASan/UBSan,
explicit collection at strict-snapshot allocation and ownership boundaries,
and audit-only deterministic allocation/insertion failures:

```console
python -m tests.tooling.runtime.json_identity_gc --result /tmp/jsonx-identity-gc.json
```

Run it with each supported interpreter. It retains source and compiler hashes,
checks output privacy, live-source mutation, reference cleanup and exception
preservation through the parity tests, and leaves the production extension
untouched. Ordinary parity tests also cover
the bounded native float writer and its per-operation cache cleanup.

`json_acceleration.py` checks the installed JSON extension with the actual
deployment interpreter, including public bindings, CPython 3.14 dispatch,
repeated floats, ASCII escapes, exact decoding, strict identity and NDJSON:

```console
python /path/to/unstd/tests/tooling/runtime/json_acceleration.py --json
```

Run outside the source checkout with the `serde` extra installed. Missing native
code or a declined native fixture fails even when a portable fallback produces
correct output. The report retains interpreter/backend versions and the loaded
binary hash. Every release wheel runs this check before its full contracts;
the performance workflow retains it alongside paired installed-wheel timings.

On CPython 3.15, public JSON functions retain ordinary Python dispatch while
native batch decoding and identity snapshots check fresh global and local
monitoring masks through retained C builtins. The preflight requires that batch
admission and an accepted native array decode, without enabling the private
CPython 3.14 vectorcall, stack or dense-dictionary layouts. Native clone mixed
graph and record paths keep their Python fallback on 3.15; their direct ABI
qualification remains on 3.12–3.14.
