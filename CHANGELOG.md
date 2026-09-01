# Changelog

<!-- towncrier release notes start -->

## [1.0.5] - 2026-09-01

### Changed

- Prose and fixtures name generic things rather than one deployment's. The
  NDJSON docstring described the format by pointing at two artifacts nobody
  outside their origin has ever seen, and a crypto fixture put a private service
  in a JWT audience - so the docstring names what NDJSON is actually used for,
  and the fixture says `example` like its sibling suite already did. No code
  paths move.

## [1.0.4] - 2026-08-31

### Fixed

- The extra floors sit at the minor series rather than the patch we happened to
  develop against, and CI now proves them. 1.0.3 relaxed `==` to `>=` but left
  each floor on an exact patch, so `unstd[model]` still rejected an application
  one pydantic bugfix behind - a resolver error that reads as the application's
  fault. The API a backend offers changes at the minor, which is where a floor
  belongs.

### Added

- A `floors` CI job resolves `--resolution lowest-direct` and runs the whole
  suite against the oldest version every floor admits. A floor is a promise that
  the library works that far back, and nothing was testing it: the rest of the
  matrix resolves the newest of everything, so the floors could have been fiction
  indefinitely and only a downstream with an older pin would have found out.

## [1.0.3] - 2026-08-31

### Fixed

- Every extra declares a floor rather than an exact `==` pin. The pins were
  inherited from the monorepo this package was extracted from, where an exact
  pin is correct because a monorepo is the application. Published, they meant an
  application already on `pydantic==2.13.4` could not install `unstd[model]` at
  all - the resolver reports the conflict against the application, not against
  us - and two libraries pinning patches are simply not co-installable. The
  floors are the versions each extra is tested against; a consumer that wants an
  exact version has its own manifest and lockfile to say so in.

## [1.0.2] - 2026-08-31

### Fixed

- The package ships `py.typed`. Every surface here is annotated and CI has been
  running `mypy --strict` over all of it, and none of that reached a consumer:
  PEP 561 says to ignore the annotations of a package with no marker, so a
  strict downstream saw `jsonx.dumps` as `Any` and wrote a suppression per call
  site to get its build green.

## [1.0.1] - 2026-08-31

### Added

- `tls.client_context` takes a `minimum_version`, defaulting to the same TLS 1.3
  it always asserted, and `tls.LEGACY_MINIMUM_VERSION` names the 1.2 floor for a
  peer not yet shown to negotiate 1.3. Without it, a service that dials one such
  peer had to hand-roll a second context — which is where the parameter this
  module refuses to have gets added. Anything below 1.2 raises instead of
  building a context.

### Fixed

- `unstd.__version__` is read from install metadata instead of being a literal
  beside the one in `pyproject.toml`. The two drifted on the first patch release
  and the gate caught its own package's version, so the tag was cut and nothing
  shipped; there is now one place to edit.

## [1.0.0] - 2026-08-31

### Added

- First public release of the full library: serde, ids, crypto, time, model,
  rex, containers, rand, pack, clone, text, audio, toml, proc, fs, and iters.
  Base install stays pure stdlib; every accelerated backend is an optional extra.
- `crypto.tls` — the verifying TLS client context, with no parameter for
  skipping verification, split out of the module that used to hold it. Its mTLS
  and pinned-root tests now actually run: they mint a throwaway self-signed
  certificate, which needs `cryptography`, which nothing had declared, so all
  three had been skipping rather than passing.
- `digest.legacy_blake2b` — the one deliberate exception to "one digest",
  for an identifier already defined in terms of BLAKE2b that cannot change
  algorithm without renaming every value derived from it. Keeping that case
  inside the seam is what stops it becoming a reason to import `hashlib` next
  door and grow a second digest for new work too.
- `ruff` and `mypy --strict` now run in CI, and `release.yml` calls the CI
  workflow as a gate rather than keeping its own copy of one — so the bar a tag
  clears is the bar a pull request clears, by construction. Neither tool had ever
  run over this package; the first pass found 26 type errors.
- A `proto` extra. `timeutil`'s protobuf `Timestamp` bridge imported
  `google.protobuf` with nothing declaring it, so the two `proto_timestamp`
  functions raised `ModuleNotFoundError` on any install that had not happened to
  pick protobuf up sideways. The import is guarded now and names the extra.
- `bench` — a harness behind the speedup numbers the READMEs print. Each case is
  one accelerated call against the stdlib call it stands in for, reported as a
  ratio, with `bench/baseline.json` pinning a floor per case that `update` may
  raise and never lower. It refuses to grade a missing backend as a pass, and
  refuses to report a ratio between two functions that disagree on the answer.
- The suite runs on a base install. It could not before: four surfaces have no
  stdlib fallback and raise at import, which pytest reports as a collection
  error, so `pip install unstd && pytest` aborted before running anything — and
  the guarded-fallback paths, which are the reason the library exists, had never
  been exercised by anything. `tests/conftest.py` now decides collectability by
  probing importability, and the fallback CI leg runs the whole suite (477 tests)
  instead of a hand-maintained subset.
- Tests for `ndjson`, which had none and 0% coverage; for `timeutil`, which had
  none; and for the package's own description of itself — the top-level docstring
  listed four module groups while sixteen shipped, so the prose, the README table
  and the directory listing are now held to each other, along with the
  zero-dependency claim.

### Changed

- `crypto` no longer projects an external algorithm table. The widths it
  enforces are BLAKE3's own — 32 bytes of digest, 32 of key, 32 of tag — so they
  live beside the calls they bound (`digest.DIGEST_BYTES` / `KDF_BYTES`,
  `token.KEY_BYTES` / `TAG_BYTES`) instead of being imported from a generated
  registry. `derive_key` takes any context string and says plainly that
  registering one is the caller's job; nothing about the derivation changed.
- The crypto known-answer vectors are now the BLAKE3 team's own published
  `test_vectors.json`, vendored verbatim and attributed in NOTICE. All three
  modes — plain hash, keyed hash (the MAC), and `derive_key` — are checked
  against the authors' answers across 35 input lengths spanning the chunk and
  subtree boundaries, where the previous fixture carried seven hand-picked rows.
- `rex` is now backed by [irregex](https://github.com/The-Billy-Company/irregex)
  (`import irgx`) instead of `google-re2`; the extra installs `irregex>=2.3.0`.
  The surface, the stdlib-`re` fidelity contract, and the degrade-to-`re`
  fallback for backreference/lookaround patterns are unchanged.
- The linear-time guarantee now covers three families that previously degraded to
  backtracking `re`: `re.VERBOSE`, `re.ASCII`, and `bytes` patterns. `bytes`
  patterns compile byte-oriented, so `rb"\w+"` stops at `caf` in `café` exactly
  as stdlib does.
- Two divergences that were always true of `rex` are now documented and pinned by
  tests rather than left to be discovered: `$` matches the absolute end of the
  text (not the position before a trailing newline, which is Perl's and stdlib's
  rule), and POSIX bracket expressions like `[[:alpha:]]` are a real class to the
  linear engine but a nested-set typo to stdlib.

### Fixed

- Nine `rex` signatures annotated their `pattern` and `string` parameters as
  `DOTALL` — the regex flag — instead of `S`, the type parameter they declare.
  Ruff's `FURB167` rewrites `re.S` to `re.DOTALL`, marks the fix safe, and had
  spliced its replacement over every PEP 695 `S` in the file. The rule is now
  disabled here, with the reason written down next to it.
- `timeutil.human_now_tz` ignored the timezone it was passed. It built the right
  instant and then called `.astimezone()` with no argument, which converts back
  to local time, so every caller got local time under another zone's name.
- `timeutil.iso_fmt` stamped a literal `Z` on whatever it was handed. A
  `datetime` in any other zone came out labelled UTC while still carrying its
  original wall-clock digits — a silent offset, in the format used for the wire.
  Aware datetimes are converted to UTC first now; naive ones are relabelled.
- `timeutil`'s human format string used `%-d` and `%-I`, which are a glibc/BSD
  extension. `strftime` raises `ValueError` on both under Windows, so every
  human-facing timestamp in the module was a hard failure on one of the three
  platforms the package claims to support.
- A `bytearray` or `memoryview` pattern now raises `TypeError`, matching what
  stdlib `re` does with one. The backends accept any buffer as a pattern, so
  `rex` was quietly accepting patterns its own twin refuses — being a superset is
  a divergence too, and code written against it broke the moment the extra was
  absent and the `re` fallback took over.
- `sub` and `subn` now type a callable `repl` as receiving either backend's match
  object, which is what has always happened at runtime. The two are
  interchangeable for everything a replacement function reads (`group`, `groups`,
  `groupdict`, `span`, `expand`, `start`, `end`); only `.pos` and `.endpos` are
  stdlib-only.
- `findall` is typed `list[S | tuple[S, ...]]` rather than a union of two list
  types, so the element type survives instead of collapsing (typeshed spells the
  same fact `list[Any]`). Runtime results are unchanged.

## [0.0.1] - 2026-08-31

Parked a good name.

### Added

- Name reservation on PyPI.
