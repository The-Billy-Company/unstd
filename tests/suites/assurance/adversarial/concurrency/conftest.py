"""Every concurrent history has a replay seed and live bounded observations."""

import pytest

from tests.support.chaos.journal import ChaosJournal, journal_sink


_SKIPPED = pytest.StashKey[bool]()


@pytest.fixture
def concurrency_seed(request):
    return request.config.getoption("--concurrency-seed")


@pytest.fixture
def concurrency_journal(request, concurrency_seed):
    journal = ChaosJournal(
        concurrency_seed,
        request.node.nodeid,
        sink=journal_sink(
            getattr(request.config, "_unstd_concurrency_journal_directory", None),
            request.node.nodeid,
        ),
    )
    request.node.user_properties.append(("concurrency_seed", concurrency_seed))
    yield journal
    snapshot = journal.snapshot()
    request.node.user_properties.append(("concurrency_journal", snapshot))
    if not snapshot["events"] and not request.node.stash.get(_SKIPPED, False):
        pytest.fail("concurrent case produced no schedule or observation journal")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when in {"setup", "call"} and report.skipped:
        item.stash[_SKIPPED] = True
    return report
