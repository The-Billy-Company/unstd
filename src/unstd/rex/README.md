# `unstd.rex` — linear-time regex

Part of [`unstd`](../README.md). A drop-in for the stdlib `re` module,
backed by **irregex** — the engine we publish ourselves — so matching over
**untrusted patterns or untrusted text** is ReDoS-immune.

## What it is

`rex` mirrors the stdlib `re` surface — `compile`, `search`, `match`,
`fullmatch`, `findall`, `finditer`, `sub`, `subn`, `split`, `escape`, `purge`,
the flag/type vocabulary (`I`/`IGNORECASE` … `Pattern`, `Match`, `RegexFlag`),
and a re-exported `error` — over
[irregex](https://github.com/The-Billy-Company/irregex) (import name `irgx`).
Migrate a call site by swapping the import:

```python
import re  # →
from unstd import rex  # rex.compile(...) / rex.search(...) — same surface

if rex.search(user_supplied_pattern, untrusted_text):  # can't ReDoS on the irgx path
    ...
```

## Why it exists

Stdlib `re` is a backtracking engine, so a hostile pattern (or hostile input
against a benign pattern) can trigger **catastrophic backtracking** — exponential
match time that pins a CPU. A classic proof: `re.search(r"(a+)+$", "a"*40 + "!")`
runs ≈2⁴⁰ backtracks and effectively never returns. That is a denial-of-service
(ReDoS) the moment either the pattern or the text is attacker-influenced — an
LLM-supplied regex, a user filter, matching over tool output or a retrieved
document.

irgx runs a finite automaton instead: match time is linear in the length of the
text and independent of the pattern's shape, so the ReDoS class cannot occur on
the irgx path. `rex` puts that engine behind the exact `re` surface, so the safe
choice is a one-line import swap rather than a rewrite.

The engine used to be Google's RE2. It moved to irregex once irgx grew the
`re`-shaped surface this shim needs (multiline capture, `Match.lastindex` /
`lastgroup`, verbose mode) — at which point paying a third-party dependency for
a capability we ship ourselves stopped making sense. Nothing about the safety
claim changed; the swap **removed** `google-re2` from our dependency closure and
widened the guarantee (below).

## Backend & fallback

The `rex` extra (`pip install 'unstd[rex]'`) provides `irregex`. Its default
grammar deliberately excludes the two features that make backtracking
exponential — **backreferences** (`\1`) and **lookaround** (`(?=…)` / `(?<=…)`) —
and *declines* them (`irgx.UnsupportedPattern`) rather than failing. `rex`
degrades gracefully:

1. **Backend absent** (a base `unstd` install) → every call uses stdlib `re`.
2. **Backend present, but the pattern needs a PCRE-only feature** → irgx declines
   it at compile; `rex` transparently re-compiles _that pattern_ on stdlib `re`
   (same mechanism `serde.jsonx` uses to auto-fall-back for exact-format cases).
   Also degrades for the two `re` flags irgx has no spelling for (`LOCALE` /
   `DEBUG`).

> **The linear-time (ReDoS-immune) guarantee holds only on the irgx path.** A
> pattern that degrades to stdlib `re` — a backref, a lookaround, `LOCALE` /
> `DEBUG`, or the absent-backend case — silently loses the guarantee and is once
> again backtracking. This is a deliberate trade (faithful behavior over a hard
> failure), pinned as a regression test. If linear-time is a _hard_ requirement,
> keep the pattern inside irgx's default grammar and install the extra.

### Three families that used to degrade and no longer do

RE2 could not take these, so `rex` sent them to backtracking `re`. irgx takes
all three, faithfully, which is the one measurable behavior win of the swap:

| Family            | How it is honored                                                                 |
| ----------------- | --------------------------------------------------------------------------------- |
| `re.VERBOSE`      | `verbose=True`; class trivia is **not** stripped, matching stdlib rather than rg        |
| `re.ASCII`        | `unicode=False`, which restricts `\w \d \s \b` and `.` to ASCII exactly as `A` does     |
| `bytes` patterns  | compiled with `unicode=False` so classes stay byte-oriented — stdlib's own rule for bytes |

The `bytes` case is the one worth knowing about: irgx reads a subject as UTF-8 by
default, so without that negation `rb"\w+"` would swallow `café` whole where
stdlib stops at `caf`. The regression test matches over non-ASCII input for
exactly that reason.

## Faithfulness & divergences (each pinned in `tests/rex/test_rex.py`)

`escape` is stdlib `re.escape` verbatim; `error` is stdlib `re.error`, and
`irgx.error` never escapes (a pattern irgx refuses — declined or malformed — is
re-compiled on `re`, which raises `re.error` for the malformed case), so `except
rex.error` keeps catching every compile failure. Compiled objects mirror
`re.Pattern`/`re.Match` on both backends, and either one is accepted wherever a
pattern string is. Flags are honored by translating the stdlib bitmask into
irgx's keyword flags (`ignore_case` / `multiline` / `dotall` / `verbose`, and
`A` as `unicode=False`).

Compiles are cached like stdlib's own `re._cache` — bounded at 512, oldest out —
so a module-level call is one dict lookup. That cache is also the only memory of
a pattern irgx *declined*: irgx caches what it compiles, not what it refuses, so
without it every `rex.search(r"(\w+)\s+\1", …)` would re-attempt the compile and
raise, about 1.8 ms a call. `purge` clears it along with both backends' caches.

One projection is adapted so the drop-in claim holds: `findall` rewrites irgx's
`None` for a non-participating group to stdlib's `""` (`.groups()` already
reports `None` on both, so nothing else needed it).

| Divergence                                    | Behavior                                                                                                                                                                |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backref / lookaround pattern                  | irgx declines → `compile` returns a stdlib `re.Pattern`; **linear-time guarantee lost**                                                                                  |
| Flag irgx can't honor (`L` / `DEBUG`)          | Degrades to stdlib `re`                                                                                                                                                 |
| **`$` is the absolute end of the text**       | Stdlib follows Perl and also matches before a final `\n`; irgx follows Rust's `regex` and Go's `regexp` and does not. `(?m)…$`, `…\Z`, or stripping the newline all agree |
| **POSIX bracket expressions**                 | `[[:alpha:]]` is a real class to irgx and a nested-set typo to stdlib. Keep patterns inside the shared grammar                                                           |
| irgx-path `.flags`                            | Reflects irgx's bookkeeping rather than the stdlib bitmask. Match _behavior_ is identical — only this cosmetic attribute differs                                           |
| `re.UNICODE` on the irgx path                 | No-op (irgx is UTF-8-native for `str`). Deliberately **not** mapped to inline `(?U)`, which swaps greediness in this lineage                                             |
| Callable `repl` in `sub` / `subn`             | Receives the active backend's match object, so `sub`/`subn` type it as either. Interchangeable for `group`/`groups`/`groupdict`/`span`/`expand`/`start`/`end`; `.pos` and `.endpos` are stdlib-only |
| `bytearray` / `memoryview` pattern            | Refused with `TypeError`, as stdlib refuses it — irgx would compile it, and being a **superset** of the twin is a divergence too                                          |

The `$` and POSIX-class rows are **not** new — RE2 read both the same way, so
they were already true of `rex` before the swap and simply went unwritten. They
are pinned now so each is a decision rather than a surprise.

## Prior art

- **Thompson's automaton construction**, popularized for this use by Russ Cox,
  ["Regular Expression Matching Can Be Simple And
  Fast"](https://swtch.com/~rsc/regexp/regexp1.html) (2007): the linear-time,
  backtracking-free matching this module exists to get.
- **[irregex](https://github.com/The-Billy-Company/irregex)** — our own Zig
  implementation (import `irgx`), and the backend `rex` wraps.
- **[RE2](https://github.com/google/re2)** — Google's implementation, the
  previous backend, and the incumbent irregex benchmarks against.
- **Inline-flag translation** — the `(?i)` / `(?s)` / `(?m)` prefixes an
  untrusted pattern arrives carrying, which `rex` normalizes so a call site
  re-homed onto the linear engine keeps its meaning.
