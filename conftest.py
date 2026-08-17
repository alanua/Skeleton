from __future__ import annotations

import pytest


def pytest_configure(config):
    config.option.tbstyle = "short"
    config.option.verbose = 1


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        # Diagnostic-only disposable branch: the test has already naturally
        # failed. Abort only the diagnostic run and expose its public-safe nodeid.
        raise RuntimeError(f"SKELETON_DIAGNOSTIC_FAILED_NODEID={item.nodeid}")
