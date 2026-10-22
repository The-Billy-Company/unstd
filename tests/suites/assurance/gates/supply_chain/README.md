# Supply chain testing

The reusable `.github/workflows/supply-chain.yml` gate runs once alongside the
ordinary CI matrix and is required by `CI / Required groups`. Release calls that
same CI gate. A daily rescan checks unchanged locks against new advisories.
Repository branch protection must require the aggregate CI status for merges.

```console
uv run --no-sync python -m tests.tooling.supply_chain check --json
uv lock --check
uv run --no-sync python -m tests.tooling.supply_chain sbom --output .local/supply-chain/sbom.cdx.json
uv audit --locked --output-format json > .local/supply-chain/audit.json
uv run --no-sync python -m tests.tooling.supply_chain audit-report .local/supply-chain/audit.json
uv run --no-sync python -m pytest tests/suites/assurance/gates/supply_chain tests/suites/assurance/gates/release -q
```

The SBOM command creates the output directory. Use uv 0.13.0, the version pinned
in this gate: its audit JSON is a preview schema and a changed schema fails
validation until reviewed. The audit includes all extras, dependency groups and
platform branches without installing them. Both advisory service failures and
findings fail CI. Adverse statuses also fail, even where uv returns zero.

The offline policy admits the root editable project and hash-pinned PyPI
artifacts over HTTPS. It rejects alternate sources, malformed hashes, unresolved
edges, stale dependency identities and unreviewed resolver overrides. uv owns
full requirement and marker freshness checks. Any native Rust crates must remain
dependency-free; adding external crates requires adding an audited Rust lane
before policy admission. The regular two-day quarantine has two explicit
exceptions: the first-party `irregex` publisher and a one-day `certifi` quarantine
for the fix to GHSA-6q48-6c7w-9gw2. No advisory IDs are suppressed.

Build and release tools have separate audited lock groups. Release exports
hash-pinned build requirements, builds the sdist once, and builds every wheel
from that same sdist. cibuildwheel uses uv for isolated builds and installs its
test closure from the audited hashes. Existing native tests still run on the
produced wheels. This avoids a second source snapshot and repeated resolvers.

Before publication, the stdlib-only release verifier checks every wheel's
RECORD digests/sizes, package identity, metadata/filename/tag agreement, archive
paths, duplicates, symlinks and decompression limits. It accepts exactly one
sdist and at least one wheel; successful matrix jobs establish platform coverage.
It emits a SHA256 manifest outside `dist`:

```console
python -m tests.tooling.release verify --dist dist --tag v3.1.0 --manifest evidence/manifest.json
python -I tests/tooling/release.py verify --dist dist --tag v3.1.0 --expected-manifest evidence/manifest.json
```

Omit `--tag` for a manual build rehearsal; the verifier infers the version from
the sdist. The publishing job downloads only the verified distributions and
their evidence, checks the manifest again, then publishes through the existing
PyPI Trusted Publisher with publish attestations enabled. It installs no project
or test dependencies. Audit/SBOM/test reports stay outside the publish directory.

Tests mutate trusted inputs and release files to prove that removed hashes,
source redirects, malformed/empty audit evidence, metadata drift and archive
tampering fail. Real packaging tests reuse session-built direct and sdist-rebuilt
wheels rather than building another fixture. Checksums detect changed bytes;
advisory scans and publish attestations do not prove dependencies are benign or
establish reproducible native builds. The SBOM is an inventory of all locked
development/build/optional environments, not a claim that all are runtime deps.
Runner images, Python distributions and the manylinux repair tools supplied by
cibuildwheel's container images remain infrastructure trust inputs; this gate
does not attest to or scan their operating system contents. The macOS repair
tool is included in the audited release group and constrained during setup.
