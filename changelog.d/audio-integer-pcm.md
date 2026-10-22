Fix whole-file and streaming PCM16/PCM32 writes to preserve raw integer sample
counts with and without soundfile. Clip before narrowing and preserve integer
semantics for mixed signed and large Python integers. Independent PCM-byte tests
cover the supported backend and source-width combinations.
