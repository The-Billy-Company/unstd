"""Resource contracts use generous bounds, exact work and independent models."""

import pytest


def _assert_bounded(small, large):
    # Fixed slack absorbs allocator noise. Deliberate eager-retention controls
    # produce the same correct output and must still fail this exact bound.
    assert large["peak_bytes"] <= small["peak_bytes"] + 128 * 1024, "memory growth"
    assert large["peak_bytes"] < 2 * 1024**2, "absolute memory bound"


@pytest.mark.parametrize("operation", ["ndjson_read", "ndjson_write", "wav_blocks"])
def test_streaming_memory_stays_bounded_as_input_grows(resource_probe, operation):
    sizes = (32768, 327680) if operation == "wav_blocks" else (1000, 10000)
    results = [resource_probe("streams", operation, size=n)["result"] for n in sizes]
    for size, result in zip(sizes, results, strict=True):
        assert result["valid"]
        assert result["checksum"] == result["expected_checksum"]
        assert result["bytes_processed"] > 0
        if operation == "wav_blocks":
            assert result["frames"] == size
            assert result["max_block_frames"] <= result["blocksize"]
            assert result["early_frames"] == result["blocksize"]
            assert result["descriptors_before"] == result["descriptors_closed"] == 0
            assert result["descriptors_open"] == 1
            assert result["caller_open"]
            assert result["caller_usable"]
        else:
            assert result["count"] == result["consumed"] == size
            if operation == "ndjson_read":
                assert result["consumed_before_first"] == 0
                assert result["consumed_after_first"] == 1
            else:
                assert result["written"] == size
                assert result["first_write_consumed"] == result["max_ahead"] == 1
    _assert_bounded(*results)


@pytest.mark.parametrize("operation", ["ndjson_read", "ndjson_write"])
def test_correct_output_cannot_hide_eager_row_retention(resource_probe, operation):
    results = [
        resource_probe("streams", operation, size=n, eager=True)["result"]
        for n in (1000, 10000)
    ]
    for size, result in zip((1000, 10000), results, strict=True):
        assert result["count"] == size
        assert result["valid"]
        assert result["checksum"] == result["expected_checksum"]
    with pytest.raises(AssertionError, match="memory growth"):
        _assert_bounded(*results)


def test_repeated_writes_and_early_walk_close_release_resources(resource_probe):
    resource_probe("state", "filesystem", size=64)


def test_regex_cache_churn_is_bounded_and_purge_releases_entries(resource_probe):
    resource_probe("state", "rex_cache", size=2048)


def test_clone_class_churn_does_not_retain_dead_types(resource_probe):
    resource_probe("state", "clone_cache", size=128)


def test_deep_json_matches_interpreter_within_cpu_and_memory_budgets(resource_probe):
    resource_probe("parsers", "json_nesting", size=20000)
