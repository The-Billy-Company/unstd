Fix NumPy-backed `rand.choices` and `Generator.choices` accepting weights with
a zero or negative total: normalizing by that total flipped all-negative weights
into valid probabilities and drew by magnitude. Both engines now raise
`ValueError` for a non-positive total before consuming any draws, and an
all-zero vector no longer emits a NumPy divide warning.
