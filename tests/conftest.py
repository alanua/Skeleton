from __future__ import annotations

from pathlib import Path
import stat

import pytest

from scripts import runner_poll_github_tasks as runner


@pytest.fixture(autouse=True)
def isolate_codex_env_sanitization_from_spawn_observer(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
):
    """Keep the env-sanitization unit test focused on its own contract.

    The spawn observer has dedicated regression tests. The environment test replaces
    subprocess.run with a synthetic result and historically asserts the provider
    output byte-for-byte; letting the host's strace availability add an observer
    breadcrumb makes that unrelated test host-dependent. Disable only the observer
    flag for that one test while leaving every dedicated observer test untouched.
    """
    if request.node.name != "test_run_codex_task_sanitizes_home_edge_environment":
        yield
        return

    original_run_command = runner.run_command

    def run_command_without_spawn_observer(*args, **kwargs):
        kwargs["observe_process_spawn"] = False
        return original_run_command(*args, **kwargs)

    monkeypatch.setattr(runner, "run_command", run_command_without_spawn_observer)
    yield


def pytest_runtest_teardown(item, nextitem) -> None:
    """Restore pytest-owned ESP Lab temp directories after permission tests.

    Two installer rollback tests intentionally leave an emulated install root at 0555
    to verify that production code preserves pre-existing permissions. Pytest later
    needs owner write/execute permission to remove that temporary tree. Keep the
    production assertion intact, then repair only the test's own tmp_path during
    teardown so validation worktrees remain removable.
    """
    if item.path.name != "test_install_home_edge_esp_lab.py":
        return
    tmp_path = item.funcargs.get("tmp_path")
    if not isinstance(tmp_path, Path) or not tmp_path.exists() or tmp_path.is_symlink():
        return

    directories = [path for path in tmp_path.rglob("*") if path.is_dir() and not path.is_symlink()]
    for path in sorted(directories, key=lambda value: len(value.parts), reverse=True):
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    mode = stat.S_IMODE(tmp_path.stat().st_mode)
    tmp_path.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
