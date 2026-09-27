# Changelog

<!-- towncrier release notes start -->

## [Unreleased]

### Breaking

This is a 2.0 surface pass: every change below renames or removes a public name,
and each old name fails at import rather than changing meaning in place.

- `serde.b64` is one verb per direction. `b64s` / `b64u_s` → `encode(data, url=,
  pad=)`, `b64d` / `b64u_d` → `decode(s, url=)`, `b64text` / `b64u_text` →
  `decode_text`, `b64_json` / `unb64_json` → `encode_json` / `decode_json`.
  `pad=False` is new - the unpadded base64url JWTs and bearer tokens use, which
  callers were `rstrip("=")`-ing by hand - and url-safe `decode` now accepts
  padded and unpadded input alike. Standard `decode` stays strict.
- `ids.hash` is gone; it was three second names for `crypto.digest`. Content
  hashes are `digest.hex(data)`, short ids `digest.hex(data, n)`, raw bytes
  `digest.raw(data)` (was `sum_` / `hex_` / `hex_n` / `content_hash`).
  `digest.of_parts(parts)` is `digest.Stream(parts).raw()`; `Stream.update_all`
  went with it, and `Stream.sum_` / `hex_` are `raw` / `hex(n=None)`.
  `digest.KDF_BYTES` folded into `DIGEST_BYTES` (both were 32).
- `crypto.token.mac` / `mac_bytes` → `mac_hex` / `mac_raw`, so the return type is
  in the name as it is in `digest`. `verify_mac` takes a tag in either form.
  `equal` lives in `token` only, and `token.opaque` is
  `rand.crypto.token_urlsafe` - it was a pass-through.
- `time.dateutil` is `time.zoned`, holding only what needs `whenever`:
  `wall` / `parse` / `now` / `instant` / `to_utc` (were `zoned_wall` /
  `parse_zoned` / `zoned_now` / `parse_instant` / `zoned_to_utc`). The pure-stdlib
  half - `parse_iso`, `parse_utc`, `parse_date`, `from_epoch_s`, `from_epoch_ms` -
  moved to `timeutil`, so none of it needs the `time` extra any more.
- `timeutil.span(start, end=None, *, unit, assume_utc=False)` replaces
  `span_hours`, `span_minutes`, `age_days`, `minutes_until`, `minutes_since`, and
  `hours_between`. Those five disagreed about whether a floating stamp was UTC
  depending on which unit you asked in; `span` is strict unless told otherwise.
  `iso_range`, `lookback`, `days_range`, and `day_offset` are removed.
- `timeutil.iso(dt=None, *, ms=False)` replaces `utcnow_iso`, `utcnow_iso_ms`,
  and `iso_fmt`; `today(utc=True)` replaces `utcnow_date_iso`; `today_iso` and
  `date_fmt` are removed (`.isoformat()`); `human(tz="")` replaces `human_now`
  and `human_now_tz`.
- `timeutil.parse_date` returns `None` on bad input like every other parser
  here, instead of raising.
- `serde.structs.codec(...)` is removed; construct `structs.Codec(...)`.
- `audio.wavx` drops the stutter: `read_wav` / `write_wav` / `read_blocks` /
  `WavWriter` → `read` / `write` / `blocks` / `Writer`.
- `fs.read_bytes`, `fs.read_text`, and `fs.iter_dir` are removed. They measured
  slower than the `pathlib` methods they wrapped.
- `proc.TimeoutError` is removed. It aliased `subprocess.TimeoutExpired`, which
  does not subclass the builtin of that name.
- `iters.take(iterable, n)` takes the iterable first, like `chunked`, `windowed`,
  and `nth` beside it. The old order fails loudly (`'int' object is not
  iterable`), never silently.
- `iters.batched_with_key` is `iters.runs` - it groups consecutive runs by key;
  it never batched. Its module is `iters.consecutive`.
- `iters.partition(iterable, pred)` takes the iterable first, like every other
  combinator in `iters`. The old order raises `TypeError` at the call.
- `serde.ndjson.read(fp)` / `write(fp, rows)` → `load(fp)` / `dump(rows, fp)`,
  the same shape as `jsonx.dump(obj, fp)` and `toml.dump(obj, fp)`. `dump` still
  returns the record count.
- `timeutil.now_tz(tz)` requires its zone. Called bare it was a second spelling
  of `utcnow()`.
- `toml`'s write half is `tomlkit`'s own functions rather than wrappers around
  them, so `dumps` / `dump` accept `sort_keys=` and each name carries tomlkit's
  types. On a base install each is a placeholder that raises naming the extra,
  decided at import rather than re-checked per call.
- `unstd.crypto` no longer re-exports the `blake3` class; it was the backend,
  not a surface. The guard moved into `digest` / `token`, so `crypto.tls` - pure
  stdlib - now imports on a base install instead of failing on a missing BLAKE3.

### Performance

- `jsonx.dumps` 416 → 264 ns, `dumpb` 375 → 200 ns, `loads` 251 → 212 ns on a
  small dict (raw orjson: ~180 ns). The fast path no longer detours through a
  checking wrapper, and `loads` names its return type with an annotation instead
  of a runtime `typing.cast` call.
- `ndjson.dumpb` / binary `write` let orjson append each newline
  (`OPT_APPEND_NEWLINE`) instead of concatenating a second `bytes` per row.
- `rand`'s scalar draws are stdlib `random` on every install. Routing
  `random()` / `choice` / `shuffle` through numpy's `Generator` one value at a
  time was 5-7x slower than the stdlib draw; numpy still fills the bulk arrays.
- `timeutil.wall` / `mono` / `mono_ns` / `perf` and `rand.crypto.*` are the stdlib
  functions bound directly, with no forwarding frame.
- `timeutil.iso` formats through `isoformat` (~2.6x faster than `strftime`), and
  `epoch_ms` is exact integer math on `time_ns()`.
- `structs.Codec.encode` / `decode` are msgspec's own bound methods.
- `digest.hex(data, n)` asks the XOF for `n` bytes rather than slicing a full
  hex string, and checks the width inline.
- `jsonx.canonical` reuses one prebuilt encoder instead of letting `json.dumps`
  construct a fresh one per call: ~1.25 µs → ~0.9 µs on a small dict, same bytes.
- `b64.encode(url=True)` rides pybase64's `altchars` straight to `str`: 293 →
  192 ns for 64 bytes unpadded.
- `token.mac_hex` / `mac_raw` check the key inline - 943 → 674 ns - and `verify`
  follows (2.1 → 1.6 µs).
- `ndjson.loads` parses a clean blob in one comprehension over the backend
  decoder and strips a line only when a direct parse fails: 338 → 187 µs per
  1k records, level with a bare orjson loop. A bad line still re-walks the slow
  path, so the error and its line number are unchanged.
- `iters.windowed(step=1)` is staggered `tee` views zipped together, a C-level
  walk: 4x on 10k items. `unique_everseen` skips the key call when there is no
  key, and `last` indexes exact builtin sequences without an ABC probe (2x).
- Pure forwarders are now the function they forwarded to: `timeutil.utcnow`
  (`partial(datetime.now, UTC)`, 132 → 96 ns), `uid.parse` / `uid.derive`
  (`UUID` / `uuid5`), `iters.flatten` (`chain.from_iterable`, 2x), `zoned.now`
  (`ZonedDateTime.now`), and - with the `text` extra - `fuzz.ratio` /
  `distance` / `jaro_winkler` as the rapidfuzz kernels (~25-40% per call). Clock
  freezers that patch at the C layer (`time-machine`) still see the bound
  references. The pure-Python fallbacks are held to the kernels by a property
  test, since with the extra installed nothing else runs them. The fuzz scorers
  take their two strings positionally.
- `wavx.wrap_pcm` packs the 44-byte header with one `struct` call - byte-identical
  to `wave`, pinned across widths, channel counts, and partial frames - 2.7x
  faster on 32 KB.
- `iters.partition` is the current `itertools` recipe - verdicts teed beside the
  values and split with `compress` - so `pred` runs once per element instead of
  once per side: 533 → 459 µs on 10k items with a trivial predicate, and half the
  predicate calls whatever it costs.
- `ndjson.dumps` joins orjson's row bytes and decodes once, and a text-mode
  `dump` decodes each row's bytes rather than re-entering `jsonx.dumps`: ~20%
  on both (399 → 317 µs and 547 → 421 µs per 1k rows).
- `clone.asdict` tests dict keys and values for atoms inline instead of paying a
  call per leaf: 5.2 → 3.8 µs on a dict-heavy record.

### Fixed

- `text.fuzz.best_match` / `extract` / `cdist` keep a choice scoring exactly at
  `score_cutoff`, as documented. rapidfuzz's own cutoff drops boundary pairs
  through a float round-trip (about 15% of random short pairs on 1.1), so every
  fast path now scores in full and applies `>=` itself - at no measurable cost.
  `cdist` also returns float64, so its scores equal `ratio`'s exactly.
- `fs.atomic_write` / `atomic_writer` no longer flip the process-wide umask to
  read it when creating a new file. Setting it to 0 and back left a window in
  which any other thread creating a file got mode `0o666`/`0o777`. The temp file
  is now opened at `0o666` so the kernel applies the umask, and an existing
  destination's mode is still copied over.

## [1.1.0] - 2026-09-27

### Fixed

- `clone.deep` returns the types it was given. Its msgspec round-trip decoded a
  nested `StrEnum` as `str`, an `IntEnum` as `int`, and an `OrderedDict` or
  `defaultdict` as a plain `dict` - losing the factory with it - and the equality
  check meant to catch a lossy round-trip passed every one, because those values
  compare equal to what came back. `deep` is now an exact-type walk that never
  leaves Python objects, so there is nothing to lose.
- `python3 -m bench update --group X` no longer erases every other group's floors.
  A narrowed run now merges into the baseline instead of replacing it.

### Changed

- `clone.deep` is pure stdlib and faster where callers actually spend time:
  4.1x over `copy.deepcopy` on a small dict (was 1.2x, slower than deepcopy on
  CPython 3.14), 2.2x on pydantic models (was a failed encode then deepcopy), and
  4.4x on large JSON trees (was 5.8x on CPython 3.13 - the price of the fix above;
  on 3.14 the two tie). Dataclasses, pydantic models, and `msgspec.Struct` are
  rebuilt the way `deepcopy` rebuilds them, with the walker standing in for its
  recursion; any other node is handed to `deepcopy` on its own while its siblings
  keep walking. The `clone` extra no longer installs anything and stays declared
  so existing `unstd[clone]` requirements resolve without a warning.

### Added

- `clone.asdict`: `dataclasses.asdict`'s result on the same walker, 10x faster on
  a dataclass tree with `datetime` leaves. A custom `dict_factory`, a
  non-dataclass argument, or a namedtuple / container subclass in the tree hands
  the call to the stdlib, so the answer is always the stdlib's.

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
