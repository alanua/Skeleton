from __future__ import annotations


def pytest_configure(config):
    # Diagnostic-only branch: preserve normal collection/order/outcomes, but stop
    # after the first natural failure so the validator tail contains its nodeid.
    config.option.maxfail = 1
    config.option.tbstyle = "short"
    config.option.verbose = 1
