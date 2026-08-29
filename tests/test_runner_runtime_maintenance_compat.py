from core.runner_runtime_maintenance_compat import (
    RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_V1,
    extract_fenced_runtime_maintenance_operation,
)


def _task(operation: str, *, task_kind: str = "runtime_maintenance") -> str:
    return f"""schema: skeleton.runner_task.v1
repo: alanua/Skeleton
task_kind: {task_kind}
payload:
  operation: {operation}
"""


def test_exact_registered_operation_is_recognized() -> None:
    assert (
        extract_fenced_runtime_maintenance_operation(
            _task(RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_V1)
        )
        == RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_V1
    )


def test_unknown_runtime_maintenance_operation_fails_closed() -> None:
    assert (
        extract_fenced_runtime_maintenance_operation(
            _task("arbitrary_privileged_shell_v1")
        )
        is None
    )


def test_non_runtime_task_does_not_gain_maintenance_route() -> None:
    assert (
        extract_fenced_runtime_maintenance_operation(
            _task(
                RUNNER_CONTROLLER_REPAIR_CODEX_STATE_MOUNT_V1,
                task_kind="code_edit",
            )
        )
        is None
    )


def test_missing_or_wrong_schema_fails_closed() -> None:
    assert (
        extract_fenced_runtime_maintenance_operation(
            "task_kind: runtime_maintenance\npayload:\n  operation: runner_controller_repair_codex_state_mount_v1\n"
        )
        is None
    )
    assert (
        extract_fenced_runtime_maintenance_operation(
            "schema: wrong\ntask_kind: runtime_maintenance\npayload:\n  operation: runner_controller_repair_codex_state_mount_v1\n"
        )
        is None
    )


def test_malformed_yaml_fails_closed() -> None:
    assert extract_fenced_runtime_maintenance_operation("payload: [") is None
