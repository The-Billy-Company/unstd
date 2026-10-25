# Native text metrics

[`src/`](src/README.md) supplies the default C Levenshtein and Jaro kernels;
[`rust/`](rust/README.md) is a build-time alternative selected by
`UNSTD_TEXT_RUST=1`. Both produce `unstd.text._text_native`.

[`fuzz.py`](../../src/unstd/text/fuzz.py) owns dispatch and Python fallback.
The bundled kernels accept only exact strings; installing RapidFuzz selects
its public distance and Jaro–Winkler scorers instead. Build selection lives in
[`hatch_build.py`](../../hatch_build.py).
