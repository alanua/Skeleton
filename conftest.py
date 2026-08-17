from __future__ import annotations

import pytest


def pytest_runtest_logreport(report):
    if report.failed and report.when == "call":
        pytest.exit(f"SKELETON_DIAGNOSTIC_FAILED_NODEID={report.nodeid}", returncode=1)
