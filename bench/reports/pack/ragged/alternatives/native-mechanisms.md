# Native finite-validation experiments

These experiments retain full fresh-byte validation. They do not cache validation results or accept a caller promise that values are finite. The application capture remains private; retained harnesses reference that capture without embedding its definitions.

The measured Mac is an Apple M5 Max with 18 CPU cores and 128 GiB of memory. The optional SME feature is present and exposes sixteen streaming float32 lanes. Apple Clang 21 builds all scratch native candidates with `-O3`; exact commands and source hashes are retained.

## Mechanisms

- NEON unsigned maximum of each float32 magnitude bit pattern removes one inner-loop vector operation. A maximum below the infinity bit pattern proves every value is finite, including negative values and all NaN encodings, without floating point operations. Four independent accumulators preserve instruction parallelism. Foreign-endian loads reverse bytes before classification.
- SME uses a runtime-gated 512-bit streaming SVE reduction, with exact NEON fallback for unsupported alignment or byte order. It improves some large-body cases but requires additional ISA/compiler support.
- Synchronous GCD dispatch splits the scan among workers and includes scheduling/join time. Eight workers reduced the 32 MiB exploratory complete decoder median from 519 us to 197 us. This is an optional exploration, not a production default: thread fanout competes with application concurrency.
- Accelerate scaled dot product passed the tested finite oracle but was slower. The maximum-magnitude control also passed the oracle, but Accelerate does not guarantee IEEE NaN and infinity handling across these operations, so it is not a qualified replacement.
- Metal no-copy mapping requires page-aligned input address and length; ordinary exact Python bytes do not provide that allocation contract. Preparing a separately aligned payload outside timing would change the fresh-byte workflow.

Primary platform contracts: [Apple specifications](https://www.apple.com/macbook-pro/specs/), [Arm ACLE SME](https://arm-software.github.io/acle/main/acle.html#sme), [Metal no-copy buffer requirements](https://developer.apple.com/documentation/metal/mtldevice/makebuffer(bytesnocopy:length:options:deallocator:)?language=objc). Accelerate local SDK `vDSP.h` documents its limitations concerning IEEE NaNs and infinities.

## Direct NEON maximum comparison

`direct-max-results.json` compares actual complete caller results without a Python validator proxy. Four arms run in the same process: captured baseline, installed native, isolated same-flags reference native, isolated maximum native. Both isolated builds use identical public Python sources and compiler flags; only the NEON float32 kernel differs. There are four distinct immutable payload contents, nine alternating batches with a 25 ms target, and 7,440 adverse checks before clocks. Output object creation and release are inside timed calls.

| Numeric body | Reference us | Maximum us | Reference / maximum | Paired wins |
| --- | ---: | ---: | ---: | ---: |
| 512 B | 1.225 | 1.351 | 0.907 | 2 / 9 |
| 16 KiB | 1.193 | 1.151 | 1.037 | 8 / 9 |
| 64 KiB | 1.772 | 1.571 | 1.128 | 8 / 9 |
| 256 KiB | 4.093 | 3.420 | 1.197 | 9 / 9 |
| 4 MiB | 51.645 | 45.433 | 1.137 | 9 / 9 |
| 32 MiB | 421.641 | 403.051 | 1.046 | 8 / 9 |

The medium-body reduction is practical and consistent, especially at 4 MiB where every maximum batch was faster than every reference batch. The smallest case had substantial ambient variation and its median regressed. The largest case gains only about 5%, consistent with bandwidth becoming dominant. A single timing does not establish a universal speed ceiling or a cross-platform claim. No production source was changed by this experiment.

The complete maximum decoder achieved roughly 2.9x to 4.4x versus the captured baseline over these six sizes. Even parallel exploratory candidates did not reach the requested 100x complete decoder target.
