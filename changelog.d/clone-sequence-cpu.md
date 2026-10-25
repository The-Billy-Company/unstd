Copy scalar sequence prefixes in an allocation-free native loop and reuse
completed repeated children from the private graph memo. Live source mutation,
shared identity, positional `asdict` copying and caller memo precedence remain
covered by ordinary, forced-GC and sanitizer tests. Tighten common clone latency
guards to 10% using matched wall/CPU batch ratios, retaining raw measurements and
rejecting malformed pairs so worker drift cannot disguise a real slowdown.
