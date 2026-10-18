Fixed concurrent regular-expression cache misses exceeding the documented
512-entry limit when multiple threads selected the same oldest entry. Compiles
now publish before trimming and retry lost evictions. A bounded regression
forces the duplicate-oldest interleaving against native and stdlib backends.
