Load container backends only when their types are requested. Package-only
imports load neither backend, and Map consumers avoid importing sortedcontainers.
Public classes remain the exact upstream objects, with unchanged operations and
missing-extra errors. Add a paired startup/operation profile with Linux and macOS
evidence.
