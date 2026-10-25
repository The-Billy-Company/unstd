# `unstd.rand`

Part of [`unstd`](../README.md). One randomness surface that replaces
stdlib `random`, splitting the three needs it usually conflates into named,
legible paths.

| Path                                                                  | Backend                                             | Use for                                                     |
| --------------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------------------- |
| [`draw`](#draw--seedable-prng) (re-exported at the package top level) | numpy `Generator(PCG64)` → stdlib `random` fallback | simulations, sampling, shuffles, bulk arrays, ML/eval draws |
| [`crypto`](#crypto--the-csprng-path)                                  | **always** stdlib `secrets` (CSPRNG)                | tokens, keys, salts, nonces, OTP/reset codes                |

## Source ownership

This package stays a flat leaf; each module owns a distinct randomness concern.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public draw exports and the secure randomness module |
| [`draw.py`](draw.py) | Seedable scalar and bulk public APIs, engine selection, and state |
| [`keyed.py`](keyed.py) | Independent SplitMix64-v1 batch validation, native dispatch, and portable algorithm |
| [`crypto.py`](crypto.py) | Secure draws using stdlib `secrets` |
| [`_rand_native.pyi`](_rand_native.pyi) | Native ABI declarations; [kernel ownership](../../../native/rand/README.md) |

Run the [randomness contract suite](../../../tests/suites/product/rand/README.md)
with `uv run --no-sync pytest tests/suites/product/rand -q`.

## Backend & fallback

The **`rand` extra** (`pip install 'unstd[rand]'`) provides
[`numpy`](https://numpy.org/) — the bulk path rides its `Generator(PCG64)` for
vectorized bulk draws. Scalar functions always use stdlib `random` (Mersenne
Twister); bulk functions also use it when NumPy is absent. The bulk array helpers
return a Python `list` on that fallback and an `ndarray` with NumPy.
`HAVE_NUMPY` exposes which backend is live. The `crypto` path never
touches the extra — it is always `secrets`.

CPython builds also provide an optional native loop for `Generator.shuffle`
on ordinary lists. It calls the same live stdlib bit engine and preserves
the exact permutation and subsequent RNG state. Custom sequences, engine
hooks, missing compilers and unsupported interpreters retain stdlib behavior.
Module-level scalar exports remain directly bound stdlib methods.

## `draw` — seedable PRNG

```python
from unstd import rand

rand.seed(1234)  # single reproducibility entrypoint (both backends)
x = rand.random()  # float in [0, 1)
n = rand.randint(1, 6)  # inclusive both ends (stdlib semantics)
u = rand.uniform(-1.0, 1.0)  # float between a and b; rounding can include b
c = rand.choice(items)  # one element (original Python object)
rand.shuffle(items)  # in place
s = rand.sample(items, k=3)  # k distinct, without replacement

xs = rand.floats(10_000)  # bulk floats  — ndarray | list
ks = rand.ints(10_000, 0, 100)  # bulk ints    — ndarray | list
cs = rand.choices(items, 100, weights=w)  # bulk, with replacement (list)
```

`seed(n)` reseeds both the NumPy `Generator(PCG64)` (when present) and stdlib's
engine. Repeating the same calls with the same backend and seed reproduces the
stream; switching backends changes the bulk sequence. It never touches the
crypto path.

For reproducibility across installations, create an independent stream with
`Generator`. Its default is **always stdlib**, even with NumPy installed:

```python
run = rand.Generator(1234)  # independent stdlib stream
xs = run.floats(100)  # always a Python list
ks = run.ints(100, 0, 10)
run.seed(1234)  # reset only this instance

vectorized = rand.Generator(1234, backend="numpy")  # explicit PCG64 bulk engine
xs = vectorized.floats(100)  # always a NumPy ndarray

parallel = rand.Generator(1234, backend="numpy", bit_generator="PCG64DXSM")
fast_bulk = rand.Generator(1234, backend="numpy", bit_generator="SFC64")
keyed_stream = rand.Generator(1234, backend="numpy", bit_generator="Philox")
```

`bit_generator` accepts `"PCG64"`, `"PCG64DXSM"`, `"SFC64"`, or `"Philox"`
only with `backend="numpy"`. Leaving it at `None` preserves PCG64. Selecting a
different bit generator changes bulk values and state consumption; reseeding
keeps the selected algorithm. Module-level draws keep their existing PCG64
default. Algorithm selection happens during construction and reseeding;
ordinary draws use the same dispatch as before, with no additional instance
slot or per-draw selection.

PCG64DXSM addresses the statistical weakness of very many long parallel PCG64
streams. SFC64 is a fast option without jump/advance support; Philox supports
independent keyed streams in NumPy. This wrapper seeds Philox normally; it does
not expose NumPy's separate key/counter controls. Performance depends on
distribution, batch size, platform, construction and conversion costs. These
choices are all simulation PRNGs and must never generate tokens or keys.
See NumPy's [engine comparison](https://numpy.org/doc/stable/reference/random/performance.html)
and [PCG64DXSM rationale](https://numpy.org/doc/stable/reference/random/upgrading-pcg64.html).

Requesting `backend="numpy"` without NumPy raises `ImportError` with installation
instructions. Every instance has the same scalar and bulk methods as the module,
and a read-only `backend` property. Instances never change module-level draws,
other instances, stdlib's global random engine, or `crypto`. All scalar methods
use the instance's stdlib engine. With `backend="stdlib"`, scalar and bulk draws
share that engine; with `backend="numpy"`, bulk draws use the separately selected
NumPy engine.
Both engines receive the same seed. Reproducibility requires the same backend,
bit generator, call sequence, and compatible engine versions; selecting NumPy does not promise
stdlib's values. Negative integer seeds are accepted by stdlib and rejected by
NumPy. A failed `seed()` call preserves both engines' previous state.

Bulk counts must be non-negative integers. `floats` requires `high >= low`;
both bounds and their difference must be finite, including for zero draws.
Floating-point rounding can include `high`, as with stdlib `uniform`.
`ints` requires integer bounds and a non-empty interval, including for zero
draws; equal bounds are valid with `endpoint=True`. NumPy integer draws use
its default `int64` dtype, while stdlib supports arbitrary-sized integers.
Weight validation retains the selected engine's behavior. For weighted draws,
use finite non-negative weights with a positive
total and one weight per population item; exact extreme-value limits and errors
can differ between engines. A non-positive total raises `ValueError` on both.

### Independent keyed integer batches

When each item needs one reproducible integer from its own key, pass `seeds`
to the existing `ints` API instead of constructing a generator for every key:

```python
keys = [1234, 5678, 9012]
counts = run.ints(len(keys), 1, 4, endpoint=True, seeds=keys)
# Also available as rand.ints(..., seeds=keys).
```

This explicit opt-in uses **SplitMix64-v1**, a different simulation stream from
`Generator(key).randint(...)`. Each key must support `operator.index` and fit
unsigned 64 bits; `n` must equal the number of keys. The effective inclusive
output bounds must fit signed int64. Validation applies to empty batches too.
It returns the selected backend's usual list or int64 ndarray. A native kernel
accelerates batches without a NumPy dependency; a portable Python implementation
produces identical values when the kernel is absent.

Each key starts a local SplitMix64 state, adds `0x9E3779B97F4A7C15`, and applies
the standard two-multiply finalizer. For interval width `w`, words below
`2**64 % w` are rejected and the accepted word is reduced modulo `w`.
Rejection advances only that key's local state. Reordering or chunking a batch
preserves each key's value, and duplicate keys repeat values. Calls leave both
ordinary engines untouched; `seed()` does not affect keyed results. This path
is for simulation and deterministic selection; secure values use `crypto`.

The [backend qualification](../../../bench/reports/rand/) separates
keyed count-phase gains from complete consumer costs and unchanged-stream gains.

The bulk helpers `floats`/`ints` return a NumPy `ndarray` on the NumPy backend and
a Python `list` on the stdlib fallback, with different seeded sequences. These
contracts are checked under
[`tests/suites/product/rand/`](../../../tests/suites/product/rand/README.md).

## `crypto` — the CSPRNG path

```python
from unstd import rand

tok = rand.crypto.token_hex()  # 2*n hex chars (n=32 default)
raw = rand.crypto.token_bytes(16)  # n secure bytes
url = rand.crypto.token_urlsafe()  # URL-safe base64 token
i = rand.crypto.below(100)  # secure int in [0, n), no modulo bias
b = rand.crypto.bits(128)  # secure int with k random bits
e = rand.crypto.choice(seq)  # secure uniform element
```

Always stdlib `secrets` — a CSPRNG over `os.urandom`. **Unseedable by design:**
`rand.seed(...)` has no effect here, because reproducibility and cryptographic
secrecy are mutually exclusive. Keeping the security-sensitive path in a separate,
seed-immune module means a seeded simulation can never accidentally weaken a token.

## Prior art

- **NumPy `Generator` / `PCG64`** — Melissa O'Neill, _"PCG: A Family of Simple Fast
  Space-Efficient Statistically Good Algorithms for Random Number Generation"_
  (2014). The bulk backend.
- **stdlib `secrets`** — [PEP 506](https://peps.python.org/pep-0506/), a CSPRNG
  wrapper over `os.urandom`. The crypto path.
- **stdlib `random`** — the Mersenne Twister (Matsumoto & Nishimura, 1998). The
  fallback + reproducibility twin.
