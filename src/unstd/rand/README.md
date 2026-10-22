# `unstd.rand`

Part of [`unstd`](../README.md). One randomness surface that replaces
stdlib `random`, splitting the three needs it usually conflates into named,
legible paths.

| Path                                                                  | Backend                                             | Use for                                                     |
| --------------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------------------------- |
| [`draw`](#draw--seedable-prng) (re-exported at the package top level) | numpy `Generator(PCG64)` → stdlib `random` fallback | simulations, sampling, shuffles, bulk arrays, ML/eval draws |
| [`crypto`](#crypto--the-csprng-path)                                  | **always** stdlib `secrets` (CSPRNG)                | tokens, keys, salts, nonces, OTP/reset codes                |

## Backend & fallback

The **`rand` extra** (`pip install 'unstd[rand]'`) provides
[`numpy`](https://numpy.org/) — the bulk path rides its `Generator(PCG64)` for
vectorized bulk draws. Scalar functions always use stdlib `random` (Mersenne
Twister); bulk functions also use it when NumPy is absent. The bulk array helpers
return a Python `list` on that fallback and an `ndarray` with NumPy.
`HAVE_NUMPY` exposes which backend is live. The `crypto` path never
touches the extra — it is always `secrets`.

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
```

Requesting `backend="numpy"` without NumPy raises `ImportError` with installation
instructions. Every instance has the same scalar and bulk methods as the module,
and a read-only `backend` property. Instances never change module-level draws,
other instances, stdlib's global random engine, or `crypto`. All scalar methods
use the instance's stdlib engine. With `backend="stdlib"`, scalar and bulk draws
share that engine; with `backend="numpy"`, bulk draws use a separate PCG64 engine.
Both engines receive the same seed. Reproducibility requires the same backend,
call sequence, and compatible engine versions; selecting NumPy does not promise
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
can differ between engines.

The bulk helpers `floats`/`ints` return a NumPy `ndarray` on the NumPy backend and
a Python `list` on the stdlib fallback, with different seeded sequences. These
contracts are checked in
[`test_rand.py`](../../../tests/suites/product/rand/unit/test_rand.py).

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
