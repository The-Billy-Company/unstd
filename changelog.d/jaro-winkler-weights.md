The portable Jaro-Winkler scorer now validates `prefix_weight` within `0..1`
before scoring, matching RapidFuzz even for empty or identical strings. NaN
weights preserve RapidFuzz's default cutoff behavior instead of leaking NaN
scores when the prefix bonus applies.
