from __future__ import annotations

import pytest

_FAILED_NODEID: str | None = None


def pytest_configure(config):
    # Diagnostic-only branch: preserve normal collection/order/outcomes while
    # keeping the run bounded at the first natural failure.
    config.option.maxfail = 1
    config.option.tbstyle = "short"
    config.option.verbose = 1


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global _FAILED_NODEID
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed and _FAILED_NODEID is None:
        _FAILED_NODEID = item.nodeid
        # Preserve failed outcome; suppress traceback/private values only.
        report.longrepr = "AssertionError: diagnostic_nodeid_redacted"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if _FAILED_NODEID is not None:
        # Match the canonical validator's existing safe nodeid parser.
        terminalreporter.write_line(f"{_FAILED_NODEID} FAILED")
