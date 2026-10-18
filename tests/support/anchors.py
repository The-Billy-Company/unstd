"""One depth-independent anchor for repository artifacts used by tests."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
PRODUCT_ROOT = TESTS_ROOT / "suites" / "product"
