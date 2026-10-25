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

For development on CPython 3.13, use the project's pinned patched interpreter.
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
