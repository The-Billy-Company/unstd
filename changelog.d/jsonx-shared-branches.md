JSON encoding can reuse completed flat branches within a single call for
eligible shared-row lists, retaining exact output, recursion and logical visit
limits, fresh output allocation and visibility of mutations between calls.
Safe ASCII strings use bulk copies. Wide JSON record decoding now proves each
child before its siblings, avoiding a redundant parse caused by the previous
64-entry sibling queue. Benchmarks label shared and independent inputs
separately and retain the existing workloads.
