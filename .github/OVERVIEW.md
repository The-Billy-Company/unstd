# GitHub configuration

This guide is named `OVERVIEW.md` because GitHub gives a README in `.github/`
priority over the repository's root `README.md` on the project page.

Repository automation lives in [`workflows/`](workflows/README.md): CI, release
verification and publishing, dependency audits, and bounded fuzz, chaos, and
concurrency campaigns. Workflow files define their own triggers and permissions.

The main [project guide](../README.md) explains the package; the
[test guide](../tests/README.md) explains suite ownership and local equivalents
of the automated checks.

## Qualified audio backend

The reusable [audio security workflow](workflows/audio-security.yml) is required
by `CI / Required groups`, including CI calls made before release. Its Linux
reference job authenticates the pinned libsndfile 1.2.2 source and exact upstream
fix, builds the reviewed backport with all required codecs, and stages a patched
SoundFile wheel. Python build and test dependencies come from hashed exports of
the committed lock in an isolated environment.

Only that successful exact-input build can emit the same-job trusted provenance
policy. After installing its authenticated wheel, the preflight resolves
SoundFile's live native symbol and hashes the actual loaded DSO before the real
audio suite runs. Missing, untrusted, or mismatched provenance fails the job.
The workflow retains the checked wheel, build evidence, runtime identity and test
report; it does not publish or add a version-only bypass. Qualification applies
to the reference Linux host and its external codec libraries, not to untested
platforms or arbitrary SoundFile wheels.
