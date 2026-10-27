Portable Jaro–Winkler matching now indexes long exact strings instead of
repeatedly scanning characters that already matched. Matching takes linear time
and storage even for long repetitive text, including through `best_match`,
`extract` and `cdist` with `scorer=fuzz.jaro_winkler`. Scores and greedy match
order are preserved. Short native calls and the RapidFuzz public binding retain
their existing implementation.

With the `text` extra, `best_match`, `extract` and `cdist` now route the built-in
`jaro_winkler` scorer through RapidFuzz's native batch kernels for ordinary lists
and tuples of strings. This accelerates contact/name matching on Linux and
macOS while preserving scores, cutoffs and stable ties. Custom scorers and
general iterables keep their previous behavior; native matrices require NumPy.
