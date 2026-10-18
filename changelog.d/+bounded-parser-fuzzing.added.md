Add eleven executable parser/token fuzz targets with independent oracles,
authenticated synthetic seed corpora, finite pytest replay, deterministic mutations,
and isolated Atheris campaigns. Workers preserve exact failures and enforce
wall/input deadlines, POSIX CPU limits and Linux address-space limits. CI runs
bounded pull-request campaigns and deeper nightly coverage campaigns, retaining
reports and generated corpora. Harness canaries verify that incorrect results,
unexpected exceptions, crashes and exhausted budgets fail the run.

Retain confirmed surrogate-region regex and truncated PCM frame findings as
module-owned regression tests and corpus entries; select faithful regex recovery
before exposing lazy match projections and reject incomplete PCM frames explicitly.
