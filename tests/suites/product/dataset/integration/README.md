# Dataset publication and source ownership

Real filesystem imports prove that readers retain old snapshots across atomic
replacement, failed producers preserve published bytes and no temporary files
remain after failures. Gzip imports use the existing NDJSON stream parser.
