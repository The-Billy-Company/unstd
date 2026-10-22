# GitHub Actions workflows

| Workflow | Purpose and triggers |
| --- | --- |
| [`ci.yml`](ci.yml) | Pull requests, pushes to `main`, manual runs, and reusable calls. Runs quality and inventory checks, grouped Python 3.12–3.14 tests, dependency floors and base-install tests, resource bounds, native safety checks, and the opt-in Rust experiment. Calls the supply-chain workflow. |
| [`release.yml`](release.yml) | Version tags (`v*`) and manual runs. Requires CI, builds an sdist and platform wheels, verifies archive contents and checksums, then publishes verified version-tag refs through the `pypi` environment using Trusted Publishing. Manual branch runs build and verify; a manual run against a version tag can also publish. |
| [`supply-chain.yml`](supply-chain.yml) | Reusable, manual, and daily audit runs. Checks dependency trust and lock freshness, writes a CycloneDX SBOM, audits the lock, and tests tamper rejection. |
| [`fuzz.yml`](fuzz.yml) | Pull requests, pushes to `main`, daily schedules, and manual runs. Runs deterministic and Atheris campaigns for installed and stdlib backends on Linux/Python 3.12. |
| [`chaos.yml`](chaos.yml) | Pull requests, pushes to `main`, daily schedules, and manual runs. Exercises fault/recovery campaigns for installed and stdlib backends on Linux and macOS. |
| [`concurrency.yml`](concurrency.yml) | Pull requests, pushes to `main`, daily schedules, and manual runs. Exercises bounded thread/process histories for both backends on Linux and macOS. |
| [`performance.yml`](performance.yml) | Product, benchmark and dependency pull requests, plus manual reference comparisons. Builds each revision's own native modules, freezes the comparison revision and verifies every public operation on the same worker with identical workloads and dependencies. Retains baselines and reports on failure. |

Keep suite ownership in [`inventory.toml`](../../tests/tooling/inventory.toml)
and CI grouping in [`ci.py`](../../tests/tooling/ci.py). The
[test guide](../../tests/README.md) documents local commands and retained reports;
the campaign READMEs cover budgets, replay, and failure artifacts. Action pins,
permissions, matrices, and schedule times are defined in the YAML files.
