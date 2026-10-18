---
doc_radar:
  sentinels:
    - file: src/unstd/rex/_faithful.py
      contains: ['_parser.parse(pattern, flags)', 'parsed.getwidth()[0] == 0', 'started = True', 'if started:']
    - file: src/unstd/rex/__init__.py
      contains: ['python = _re.compile(pattern, flags)', 'profile, _irgx.error', '_MAXCACHE = 512']
    - file: pyproject.toml
      contains: ['"irregex>=2.6.0"']
---

# `unstd.rex`

We put our [irregex](https://github.com/The-Billy-Company/irregex) engine behind
Python's `re` surface. Install `pip install 'unstd[rex]'`, then swap the import:

```python
from unstd import rex

rx = rex.compile(r"(?a:\w+)")
assert rx.findall("café 123") == ["caf", "123"]
```

`compile`, `search`, `match`, `fullmatch`, `findall`, `finditer`, `sub`, `subn`,
`split`, `escape`, `purge`, flags and compile errors keep their familiar shape.
A base installation has zero dependencies and uses stdlib `re`.

## Python owns the meaning

We validate with Python's compiler and inspect its parsed operations, including
scoped ASCII, Unicode and case flags. The native engine keeps supported regular
patterns, bytes, faithful ASCII classes and verbose mode; the compiled facade
retains your source and Python flag metadata.

Unicode word/digit classes and boundaries run natively on ASCII subjects and
use Python on non-ASCII subjects. Unicode whitespace and cased Unicode
`IGNORECASE` patterns use Python even on ASCII input; their semantics differ
between the engines, and we use the running interpreter's Unicode version.

Empty-match patterns and nullable repeats use Python's iteration and capture
rules. Backreferences, lookaround, unsupported operations and `LOCALE`/`DEBUG`
also fall back, as do native operations that decline a requested match shape.

## Safety follows execution

Native matching uses finite automata, so hostile patterns cannot trigger
catastrophic backtracking there. `rex.search(r"(a+)+$", "a" * 40 + "!")` keeps
that native safety win; the tests prove the same call hangs on stdlib `re` in a
separate process.

Every fallback uses Python's backtracking engine and loses that guarantee.
Installing the extra alone is insufficient: use supported regular patterns and
bytes or faithful ASCII expressions over encodable input when linear matching
is required.

## Recovery keeps side effects intact

Python strings can contain lone surrogates that UTF-8 cannot encode. We recover
on Python before producing a match or starting a replacement callback; a
callback's own exception propagates unchanged, and callbacks are never retried.

`findall` converts absent groups to empty strings for compiled and module calls;
`groups()` still reports `None`. Negative substitution or split limits leave the
input unchanged, just as Python does.

## Existing boundaries

On the native path, `$` means absolute end rather than the position before a
final newline. Use `(?m)…$` for line ends or `…\Z` for absolute end; POSIX bracket
expressions also retain their native class meaning instead of Python's
nested-set spelling.

Native matches provide `group`, `groups`, `groupdict`, `span`, `expand`, `start`
and `end`; `.pos` and `.endpos` remain stdlib-only. A callable replacement sees
the active backend's match, whose `.re.flags` uses native bookkeeping.

`error` and `escape` are stdlib's own objects, native-only syntax is refused by
Python, and compiled patterns round-trip through every module verb. Compiles and
declines share a bounded cache of 512 entries; `purge` clears it and both engines.
Concurrent misses compile independently, then retry oldest-entry eviction until
the cache is within that bound. Retained compiled patterns and matches remain
usable after eviction or purge.

## Where it comes from

We replaced RE2 once irregex supplied the existing API, retiring that dependency.
The safety primitive is [Thompson's automaton construction, explained by Russ
Cox](https://swtch.com/~rsc/regexp/regexp1.html); the language contract comes from
[Python's regex documentation](https://docs.python.org/3/library/re.html).
