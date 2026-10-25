# GitHub Actions workflows

| Workflow | Purpose and triggers |
| --- | --- |
| [`ci.yml`](ci.yml) | Pull requests, pushes to `main`, manual runs, and reusable calls. Runs quality and inventory checks, grouped Python 3.12–3.14 tests, dependency floors and base-install tests, macOS/Windows public contracts, each declared extra installed alone (plus backend-selecting combinations), resource bounds, native safety checks, and the opt-in Rust experiment. Contracts and sanitizer jobs use three complete, disjoint test partitions with unchanged per-test budgets. Calls the supply-chain and static-analysis workflows. |
| [`release.yml`](release.yml) | Version tags (`v*`) and manual runs. Builds the sdist and platform/interpreter wheel shards alongside CI, then requires successful exact-commit CI and verified archives before Trusted Publishing through `pypi`. Reuses repository-owned push/manual CI started within the past hour; otherwise calls the full CI gate. Manual branch runs build and verify; a manual version-tag run can also publish. |
| [`windows-arm-cache.yml`](windows-arm-cache.yml) | Warms all three Windows ARM interpreter caches on dependency/build configuration changes to `main`, twice weekly to prevent cache expiry, or manually. Shares authenticated source-built test dependencies and Python downloads with releases. |
| [`static-analysis.yml`](static-analysis.yml) | Reusable, manual, and weekly full-source SAST. Runs Bandit, CodeQL for Python/C/C++/Rust/Actions, Rust Clippy, and Python duplicate-code enforcement. Findings and incomplete evidence block CI and releases. |
| [`supply-chain.yml`](supply-chain.yml) | Reusable, manual, and daily audit runs. Checks dependency trust and lock freshness, writes a CycloneDX SBOM, audits the lock, and tests tamper rejection. |
| [`fuzz.yml`](fuzz.yml) | Pull requests, pushes to `main`, daily schedules, and manual runs. Runs deterministic campaigns for installed and stdlib backends on Linux/Python 3.12 and 3.14, plus Atheris campaigns on Python 3.12. |
| [`chaos.yml`](chaos.yml) | Pull requests, pushes to `main`, daily schedules, and manual runs. Exercises fault/recovery campaigns for installed and stdlib backends on Linux and macOS. |
| [`concurrency.yml`](concurrency.yml) | Pull requests, pushes to `main`, daily schedules, and manual runs. Exercises bounded thread/process histories for both backends on Linux and macOS. |
| [`performance.yml`](performance.yml) | Product, native source, benchmark and dependency pull requests, plus manual reference comparisons. Independently builds and compares revisions on Linux and macOS with Python 3.13 and 3.14, including the distinct 3.14 C fast paths. Each platform/interpreter pair uses its own frozen worker baseline with identical workloads and dependencies and retains separate evidence; a regression in one job does not cancel the others. |

Keep suite ownership in [`inventory.toml`](../../tests/tooling/suites/inventory.toml)
and CI grouping in [`ci/groups.py`](../../tests/tooling/ci/groups.py). The
[test guide](../../tests/README.md) documents local commands and retained reports;
the campaign READMEs cover budgets, replay, and failure artifacts. Action pins,
permissions, matrices, and schedule times are defined in the YAML files.

Development and default workflow jobs use Python 3.14, matching the consuming
application. Compatibility matrices continue to test Python 3.12 and 3.13;
the dependency-floor and base-install environments run on Python 3.14.
The audited uv frontend includes the pinned development Python in its bundled
download catalog; an offline gate checks every supported host platform. Cached
wheel providers upgrade their patch release before TLS and ABI admission.
The text extra retains its older floor on Python 3.12/3.13 and uses RapidFuzz
3.14 or newer on Python 3.14, where that series supplies compatible wheels.

Each normal performance comparison also gates all 14 frozen gzip consumer
shapes before the full function verification. Both installations use the same
worker, interpreter, dependencies and committed synthetic fixtures; a case above
130% of the comparison revision's wall time fails after saving its raw evidence.
The Linux/macOS and Python 3.13/3.14 jobs retain that evidence independently.
No application checkout is required. Manual `gzip_diagnostic` runs keep their
separate check name and do not satisfy the normal performance checks.

The clone comparison also checks 30 common graph and dataclass workloads with
GC enabled. It proves successful native graph, dataclass and `asdict` routes in
both installations, then fails if the median matched wall or CPU ratio exceeds
110% of the comparison revision. Pairing rounds keeps worker drift from
creating a false regression. Baseline imports select that revision's own
native entrypoints. Raw timings and route evidence remain in `clone-common.json`
even when the guard fails.

Normal comparisons also require the installed current JSON kernels to pass
`tests/tooling/runtime/json_acceleration.py`, then compare JSON record workloads
between the two installed revisions with `--require-native`. A wall-time
slowdown above 15% fails after retaining the paired raw samples, stdlib controls,
binary/source hashes and dispatch evidence. Every release wheel runs the same
native qualification before its full installed-wheel tests.
Shared-row JSON cases and equal-output independent controls have a separate
paired check and retained report, with the same 15% regression limit. These
workloads detect loss of operation-local branch reuse without changing the
ordinary record fixtures.

The Linux full-extra test environments and the isolated `archives` lane
explicitly install native libarchive for runtime qualification. The Python
`archives` extra supplies its binding; it does not bundle that native library.
Base-install jobs retain their dependency-free import and missing-extra checks.

Linux native test, sanitizer, security, performance, and campaign builds install
Yasm before compiling the private ISA-L gzip decoder. The build requires Yasm
1.3 or newer, or NASM 2.11.01 or newer; release Linux x86_64 containers already
supply Yasm. The native build never downloads an assembler or codec source. The
base install matrix keeps its ordinary fallback build when no assembler is present.
The strict Linux x86 native job requires active ISA-L streaming and checks its
compiled version and patch digest against the reviewed source inputs before
running product contracts.

Linux native allocator and ASan/UBSan jobs also require the filesystem extension
to import and run the fs product contracts. Its signal/error observer is built
only for tests and preserves sanitizer preload order. Release wheel tests include
fs; the verifier requires the binary on supported Linux native wheels and rejects
it on other platforms, while retaining the portable typing stub. Compiler-failure
and pristine installed-consumer checks cover native dispatch and the Python
fallback. The [filesystem qualification](../../bench/reports/fs/production/README.md)
records the separate installed workload and sanitizer evidence.

The dependency-floor job restores the committed `uv.lock` immediately after
installing its lowest-direct environment. Tests retain those installed versions
with `--no-sync`, while release/export checks inspect the audited canonical lock
instead of rejecting a temporary lock created with another resolution policy.

Windows ARM release caches use the audited dependency and build configuration,
excluding only this package's version, so version bumps reuse compiled `blake3`
and `zlib-ng` wheels. The warmup saves caches on `main`, which release tags can
read; caches written by other tags are inaccessible. A new dependency closure
still requires a cold build. Wheel contracts run in up to four separate pytest
processes using the existing complete, disjoint partitions and unchanged test
budgets. Each release always rebuilds and tests its own wheel from its sdist.
