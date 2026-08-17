from __future__ import annotations

_FIRST_FAILURE: str | None = None


def pytest_runtest_logreport(report):
    global _FIRST_FAILURE
    if _FIRST_FAILURE is None and report.failed and report.when == "call":
        _FIRST_FAILURE = report.nodeid
        report.longrepr = f"SKELETON_DIAGNOSTIC_FAILED_NODEID={report.nodeid}"
