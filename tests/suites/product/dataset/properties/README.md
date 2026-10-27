# Dataset generated laws

Independent stdlib JSON normalization defines values across integer indexing,
ordered selection, reverse and lazy slices, bounded batches, and complete
iteration. Generated trees include large integers, finite floats, Unicode,
lone surrogates, and escaped physical newlines. The same artifact is read
through both the portable and native implementations.

```sh
python -m pytest tests/suites/product/dataset/properties -q
```
