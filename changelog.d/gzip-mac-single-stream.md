Accelerate highly repetitive gzip streams on Mac ARM with bounded C selection
of the already imported zlib-ng provider. Preserve Apple libz for ordinary
dataset streams and zlib-ng's SIMD path on Linux. Selection occurs before any
body decoding and preserves gzip framing, source read budgets, bounded output,
CRC/size checks, concatenated members, seeking and staged public `readinto`.
