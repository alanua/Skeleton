from __future__ import annotations

import atexit

_FIRST_FAILED_NODE: str | None = None


def pytest_runtest_logreport(report: object) -> None:
    global _FIRST_FAILED_NODE
    if _FIRST_FAILED_NODE is not None:
        return
    if getattr(report, "when", None) != "call" or not bool(getattr(report, "failed", False)):
        return
    nodeid = getattr(report, "nodeid", None)
    if isinstance(nodeid, str) and nodeid.startswith("tests/") and "\n" not in nodeid and "\r" not in nodeid:
        _FIRST_FAILED_NODE = nodeid[:500]


@atexit.register
def _emit_first_failed_node() -> None:
    if _FIRST_FAILED_NODE is not None:
        print(f"SKELETON_FIRST_FAILURE_NODE={_FIRST_FAILED_NODE}")
