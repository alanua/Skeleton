from __future__ import annotations

_FAILED_NODEIDS: list[str] = []


def pytest_runtest_logreport(report):
    if report.failed and report.when == "call":
        _FAILED_NODEIDS.append(report.nodeid)
        report.longrepr = f"SKELETON_DIAGNOSTIC_FAILED_NODEID={report.nodeid}"


def pytest_sessionfinish(session, exitstatus):
    if _FAILED_NODEIDS:
        print("SKELETON_DIAGNOSTIC_FAILED_NODEIDS=" + "|".join(_FAILED_NODEIDS))
