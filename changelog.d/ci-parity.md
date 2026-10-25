Fix clean-runner compatibility across supported Python versions and platforms:
align the audited uv frontend with the pinned Python download, use a compatible
RapidFuzz floor on Python 3.14, refresh cached wheel runtimes before security
admission, and retain platform/runtime dependency markers in artifact verification.
Document the new optional extras, guard Unix-only benchmark metadata, and qualify
zipfile's version-specific internal child module against its actual stdlib API.
Retain a recursive YAML merge finding as a fuzz regression while preserving safe
parser and product parity checks.
Avoid whole-block gzip engine selection for small buffered reads, compare shared
performance workloads before timing added APIs, and bound sanitizer-only stack
boundary differences without dropping discarded-source recursion checks.
Reuse the bounded weak type/version cache for native record layouts during
general graph copying, retaining Python admission and mutation invalidation.
