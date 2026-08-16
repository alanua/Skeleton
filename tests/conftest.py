from __future__ import annotations

_FIRST_FAILED_NODE: str | None = None


def pytest_runtest_logreport(report: object) -> None:
    global _FIRST_FAILED_NODE
    if _FIRST_FAILED_NODE is not None:
        return
    if getattr(report, "when", None) != "call" or not bool(getattr(report, "failed", False)):
        return
    nodeid = getattr(report, "nodeid", None)
    if not isinstance(nodeid, str) or not nodeid.startswith("tests/"):
        return
    if "\n" in nodeid or "\r" in nodeid:
        return
    _FIRST_FAILED_NODE = nodeid[:500]
    try:
        setattr(report, "longrepr", f"AssertionError: SKELETON_FIRST_FAILURE_NODE={_FIRST_FAILED_NODE}")
    except Exception:
        pass
