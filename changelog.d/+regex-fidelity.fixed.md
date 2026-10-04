We keep Python's Unicode classification, scoped flags, empty-match iteration and
nullable capture behavior across native engine upgrades. Eligible ASCII and byte
work still run natively; surrogate recovery and typed native refusals preserve
outputs without retrying replacement callbacks.
