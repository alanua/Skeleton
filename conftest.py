from __future__ import annotations

import os
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
        # Bypass pytest capture only for the public-safe nodeid so the existing
        # validator parser can report it. Do not expose traceback or values.
        os.write(2, f"{item.nodeid} FAILED\n".encode("utf-8"))
