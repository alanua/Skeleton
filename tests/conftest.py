from __future__ import annotations

_FIRST_FAILED_NODE: str | None = None


def _safe_failure_line(report: object) -> int:
    try:
        longrepr = getattr(report, "longrepr")
        traceback = getattr(longrepr, "reprtraceback")
        entries = getattr(traceback, "reprentries")
        if entries:
            fileloc = getattr(entries[-1], "reprfileloc")
            lineno = int(getattr(fileloc, "lineno"))
            if lineno > 0:
                return lineno
    except Exception:
        pass
    try:
        location = getattr(report, "location")
        lineno = int(location[1]) + 1
        return lineno if lineno > 0 else 0
    except Exception:
        return 0


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
    line = _safe_failure_line(report)
    try:
        setattr(
            report,
            "longrepr",
            f"AssertionError: SKELETON_FIRST_FAILURE_NODE={_FIRST_FAILED_NODE};LINE={line}",
        )
    except Exception:
        pass
