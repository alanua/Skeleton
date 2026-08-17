from __future__ import annotations

import pytest


def pytest_configure(config):
    # Diagnostic-only branch: preserve normal collection/order/outcomes while
    # keeping the run bounded at the first natural failure.
    config.option.maxfail = 1
    config.option.tbstyle = "short"
    config.option.verbose = 1


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    # Do not alter pass/fail. Replace only the public rendering of the first
    # natural failing call with its nodeid, avoiding traceback/private values.
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        report.longrepr = f"SKELETON_DIAGNOSTIC_FAILED_NODEID={item.nodeid}"
