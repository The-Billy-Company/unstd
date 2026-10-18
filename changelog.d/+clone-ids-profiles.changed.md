Reduce duplicate private-attribute traversal when cloning Pydantic models and
the dataclass conversion entry check. Remove redundant integer conversion,
branching, and bit assembly in the portable UUIDv7 generator. Keep the existing
APIs, direct stdlib aliases, secure entropy,
and synchronized counter behavior. Custom dataclass constructor arguments and
msgspec copy reducers defer to the stdlib copy protocol.

Add reproducible per-function clone/UUID profiles and paired workload timings,
with output validation, unchanged-signature checks, and explicit coverage gaps.
