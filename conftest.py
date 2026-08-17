from __future__ import annotations


def pytest_collection_modifyitems(config, items):
    keep = []
    drop = []
    for item in items:
        if "tests/test_runner_poll_github_tasks.py" in item.nodeid:
            keep.append(item)
        else:
            drop.append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep
