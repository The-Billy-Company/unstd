Copy scalar sequence prefixes in an allocation-free native loop and fill initial
adjacent runs from a completed private-memo child. Live source mutation,
shared identity, positional `asdict` copying and caller memo precedence remain
covered by ordinary, forced-GC and sanitizer tests. Tighten common clone latency
guards to 10% using matched wall/CPU batch ratios, retaining raw measurements and
rejecting malformed pairs so worker drift cannot disguise a real slowdown.
Keep Linux audit launchers free of inherited ASan preloads while restoring
instrumentation at every actual Python exec.
