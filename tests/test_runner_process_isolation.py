from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import pytest

from core.runner_process_isolation import (
    RunnerProcessIsolationError,
    RunnerProcessIsolator,
    RunnerProcessLimits,
    requires_process_isolation,
)


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable).resolve()
GIT = Path(shutil.which("git") or "").resolve()


def reason(
    error: pytest.ExceptionInfo[RunnerProcessIsolationError],
    code: str,
) -> None:
    assert error.value.reason_code == code


def isolator(
    *,
    output_limit_bytes: int = 1024 * 1024,
    grace: float = 0.2,
    allowed_environment_keys=(),
    limits: RunnerProcessLimits = RunnerProcessLimits(),
) -> RunnerProcessIsolator:
    assert GIT.is_absolute()
    assert GIT.is_file()

    return RunnerProcessIsolator(
        (PYTHON,),
        git_executable=GIT,
        allowed_environment_keys=allowed_environment_keys,
        limits=limits,
        output_limit_bytes=output_limit_bytes,
        termination_grace_seconds=grace,
    )


def test_route_policy_only_isolates_two_high_risk_routes() -> None:
    assert requires_process_isolation("code_edit")
    assert requires_process_isolation("repository_maintenance")
    assert not requires_process_isolation("diagnostic")
    assert not requires_process_isolation("private_memory")

    with pytest.raises(RunnerProcessIsolationError) as error:
        requires_process_isolation("unknown")

    reason(error, "UNKNOWN_ISOLATION_ROUTE")


def test_successful_isolated_command_uses_verified_cwd() -> None:
    runner = isolator()

    result = runner.run(
        task_kind="code_edit",
        argv=(
            str(PYTHON),
            "-c",
            "import os; print(os.getcwd()); print('ok')",
        ),
        worktree_root=ROOT,
        cwd=ROOT / "tests",
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        str((ROOT / "tests").resolve()),
        "ok",
    ]
    assert result.stderr == ""
    assert not result.timed_out
    assert not result.sigterm_sent
    assert not result.sigkill_sent


def test_non_isolated_route_is_rejected() -> None:
    runner = isolator()

    with pytest.raises(RunnerProcessIsolationError) as error:
        runner.run(
            task_kind="diagnostic",
            argv=(str(PYTHON), "-c", "print('no')"),
            worktree_root=ROOT,
            timeout_seconds=5,
        )

    reason(error, "ROUTE_NOT_ISOLATED")


def test_executable_must_be_absolute_and_exactly_allowlisted() -> None:
    runner = isolator()

    with pytest.raises(RunnerProcessIsolationError) as relative:
        runner.run(
            task_kind="code_edit",
            argv=("python3", "-c", "print('no')"),
            worktree_root=ROOT,
            timeout_seconds=5,
        )

    reason(relative, "EXECUTABLE_PATH_NOT_ABSOLUTE")

    with pytest.raises(RunnerProcessIsolationError) as unallowlisted:
        runner.run(
            task_kind="code_edit",
            argv=(str(GIT), "--version"),
            worktree_root=ROOT,
            timeout_seconds=5,
        )

    reason(unallowlisted, "EXECUTABLE_NOT_ALLOWLISTED")


def test_environment_is_minimal_and_does_not_inherit_secrets(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "RUNNER_SECRET_SHOULD_NOT_LEAK",
        "private-value",
    )

    runner = isolator(
        allowed_environment_keys=("RUNNER_TEST_VALUE",),
    )

    script = """
import json
import os
print(json.dumps({
    "custom": os.environ.get("RUNNER_TEST_VALUE"),
    "secret": os.environ.get("RUNNER_SECRET_SHOULD_NOT_LEAK"),
    "path": os.environ.get("PATH"),
    "home": os.environ.get("HOME"),
}, sort_keys=True))
"""

    result = runner.run(
        task_kind="repository_maintenance",
        argv=(str(PYTHON), "-c", script),
        worktree_root=ROOT,
        timeout_seconds=5,
        environment={"RUNNER_TEST_VALUE": "allowed"},
    )

    payload = json.loads(result.stdout)

    assert payload["custom"] == "allowed"
    assert payload["secret"] is None
    assert payload["path"] == ""
    assert "skeleton-runner-isolated-" in payload["home"]


def test_unallowlisted_environment_variable_is_rejected() -> None:
    runner = isolator()

    with pytest.raises(RunnerProcessIsolationError) as error:
        runner.run(
            task_kind="code_edit",
            argv=(str(PYTHON), "-c", "print('no')"),
            worktree_root=ROOT,
            timeout_seconds=5,
            environment={"TOKEN": "secret"},
        )

    reason(error, "ENVIRONMENT_KEY_NOT_ALLOWLISTED")


def test_reserved_environment_key_cannot_be_allowlisted() -> None:
    with pytest.raises(RunnerProcessIsolationError) as error:
        isolator(allowed_environment_keys=("PATH",))

    reason(error, "FORBIDDEN_ENVIRONMENT_KEY")


def test_cwd_cannot_escape_worktree(tmp_path: Path) -> None:
    runner = isolator()

    with pytest.raises(RunnerProcessIsolationError) as error:
        runner.run(
            task_kind="code_edit",
            argv=(str(PYTHON), "-c", "print('no')"),
            worktree_root=ROOT,
            cwd=tmp_path,
            timeout_seconds=5,
        )

    reason(error, "PROCESS_CWD_ESCAPES_WORKTREE")


def test_fake_worktree_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("not a gitdir", encoding="utf-8")

    runner = isolator()

    with pytest.raises(RunnerProcessIsolationError) as error:
        runner.run(
            task_kind="code_edit",
            argv=(str(PYTHON), "-c", "print('no')"),
            worktree_root=tmp_path,
            timeout_seconds=5,
        )

    reason(error, "UNVERIFIED_WORKTREE_ROOT")


def test_timeout_terminates_entire_process_group() -> None:
    runner = isolator(grace=0.1)

    script = """
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(30)
"""

    result = runner.run(
        task_kind="code_edit",
        argv=(str(PYTHON), "-c", script),
        worktree_root=ROOT,
        timeout_seconds=0.5,
    )

    assert result.timed_out
    assert result.sigterm_sent
    assert result.sigkill_sent
    assert result.returncode == -signal.SIGKILL


def test_output_capture_is_bounded() -> None:
    runner = isolator(output_limit_bytes=128)

    with pytest.raises(RunnerProcessIsolationError) as error:
        runner.run(
            task_kind="code_edit",
            argv=(
                str(PYTHON),
                "-c",
                "print('x' * 4096)",
            ),
            worktree_root=ROOT,
            timeout_seconds=5,
        )

    reason(error, "PROCESS_OUTPUT_LIMIT_EXCEEDED")


def test_resource_limits_are_applied() -> None:
    limits = RunnerProcessLimits(
        cpu_seconds=5,
        address_space_bytes=512 * 1024 * 1024,
        file_size_bytes=1024 * 1024,
        open_files=64,
        process_count=16,
    )

    runner = isolator(
        limits=limits,
        output_limit_bytes=4096,
    )

    script = """
import json
import resource

print(json.dumps({
    "core": resource.getrlimit(resource.RLIMIT_CORE)[0],
    "nofile": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
    "nproc": resource.getrlimit(resource.RLIMIT_NPROC)[0],
}, sort_keys=True))
"""

    result = runner.run(
        task_kind="repository_maintenance",
        argv=(str(PYTHON), "-c", script),
        worktree_root=ROOT,
        timeout_seconds=5,
    )

    payload = json.loads(result.stdout)

    assert payload["core"] == 0
    assert payload["nofile"] <= 64
    assert payload["nproc"] <= 16



def test_completed_parent_cannot_leave_descendant_running(
    tmp_path: Path,
) -> None:
    runner = isolator(grace=0.1)
    marker = tmp_path / "descendant-leaked"

    child_script = """
import pathlib
import sys
import time

time.sleep(0.5)
pathlib.Path(sys.argv[1]).write_text("leaked", encoding="utf-8")
"""

    parent_script = """
import subprocess
import sys

subprocess.Popen(
    (
        sys.executable,
        "-c",
        sys.argv[2],
        sys.argv[1],
    )
)
"""

    result = runner.run(
        task_kind="code_edit",
        argv=(
            str(PYTHON),
            "-c",
            parent_script,
            str(marker),
            child_script,
        ),
        worktree_root=ROOT,
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert result.sigterm_sent or result.sigkill_sent

    time.sleep(0.8)
    assert not marker.exists()
