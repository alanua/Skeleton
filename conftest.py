from __future__ import annotations

_FAILED_NODEIDS: list[str] = []


def pytest_runtest_logreport(report):
    if report.failed and report.when == "call":
        _FAILED_NODEIDS.append(report.nodeid)


def pytest_terminal_summary(terminalreporter):
    if _FAILED_NODEIDS:
        terminalreporter.write_sep("=", "SKELETON DIAGNOSTIC FAILING NODEIDS")
        for nodeid in _FAILED_NODEIDS:
            terminalreporter.write_line(f"FAILED_NODEID={nodeid}")
