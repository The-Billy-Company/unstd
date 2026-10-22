"""Every chaos case publishes its replay seed and bounded fault observations."""

import pytest

from tests.support.chaos.journal import ChaosJournal, journal_sink


@pytest.fixture
def chaos_seed(request):
    return request.config.getoption("--chaos-seed")


@pytest.fixture
def chaos_journal(request, chaos_seed):
    journal = ChaosJournal(
        chaos_seed,
        request.node.nodeid,
        sink=journal_sink(
            getattr(request.config, "_unstd_chaos_journal_directory", None),
            request.node.nodeid,
        ),
    )
    request.node.user_properties.append(("chaos_seed", chaos_seed))
    yield journal
    snapshot = journal.snapshot()
    request.node.user_properties.append(("chaos_journal", snapshot))
    if not snapshot["events"] and not request.node.stash.get(_SKIPPED, False):
        pytest.fail("chaos case produced no fault schedule or observation journal")


_SKIPPED = pytest.StashKey[bool]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when in {"setup", "call"} and report.skipped:
        item.stash[_SKIPPED] = True
    return report
