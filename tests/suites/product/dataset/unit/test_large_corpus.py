"""A streamed index spanning multiple I/O chunks remains complete and verifiable."""

from hashlib import sha256

from unstd.dataset import Dataset, build


def test_large_streamed_corpus_retains_complete_offsets_and_checksum(tmp_path):
    path = tmp_path / "large.dataset"
    count = 140_000
    assert build(path, iter(range(count))) == count
    with Dataset(path) as dataset:
        assert len(dataset) == count
        assert dataset.take([0, 131_071, 131_072, -1]) == [
            0,
            131_071,
            131_072,
            count - 1,
        ]
        assert dataset[-3:] == [count - 3, count - 2, count - 1]
        dataset.verify()
    frame = path.read_bytes()
    assert sha256(frame[56:]).digest() == frame[24:56]
