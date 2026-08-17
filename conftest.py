from __future__ import annotations

import pytest


def pytest_configure(config):
    # Diagnostic-only disposable branch: preserve collection/fixtures/outcomes
    # and stop after the first natural failure.
    config.option.maxfail = 1
    config.option.tbstyle = "short"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        node_hex = item.nodeid.encode("utf-8").hex()
        # Preserve FAILED; expose only an alphanumeric encoding accepted by the
        # public receipt sanitizer. No traceback or test values are rendered.
        report.longrepr = f"AssertionError: NODEHEX_{node_hex}"
