# GitHub configuration

This guide is named `OVERVIEW.md` because GitHub gives a README in `.github/`
priority over the repository's root `README.md` on the project page.

Repository automation lives in [`workflows/`](workflows/README.md): CI, release
verification and publishing, dependency audits, and bounded fuzz, chaos, and
concurrency campaigns. Workflow files define their own triggers and permissions.

The main [project guide](../README.md) explains the package; the
[test guide](../tests/README.md) explains suite ownership and local equivalents
of the automated checks.
