We keep matrix tests on the managed Python runtime selected by setup-uv, so the runner's older OpenSSL cannot replace it. The test dependency floor now includes the `pytest.importorskip(exc_type=...)` API used by the suite; the TLS admission policy stays unchanged.

We select the isolated audio environment after the runner starts, so GitHub can validate the reusable security workflow before running it. The audio job also selects managed Python explicitly.
