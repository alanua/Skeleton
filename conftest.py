from __future__ import annotations

_FAILED_NODEIDS: list[str] = []


def pytest_runtest_logreport(report):
    if report.when == "call" and report.failed:
        _FAILED_NODEIDS.append(report.nodeid)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    terminalreporter.write_sep("=", "SKELETON DIAGNOSTIC FAILING NODEIDS")
    if not _FAILED_NODEIDS:
        terminalreporter.write_line("DIAG_FAIL_NODEID=NONE")
        return
    for nodeid in _FAILED_NODEIDS:
        terminalreporter.write_line(f"DIAG_FAIL_NODEID={nodeid}")
