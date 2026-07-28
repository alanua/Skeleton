from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/scheduler-runtime-launch.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_is_exact_main_and_registered_runner_only() -> None:
    text = _text()
    assert "github.repository == 'alanua/Skeleton'" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "hetzner-agent-runner-1" in text
    assert "self-hosted" in text
    assert "git rev-parse HEAD" in text
    assert 'test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"' in text
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in text


def test_workflow_has_minimal_permissions_and_no_inputs() -> None:
    text = _text()
    assert "contents: read" in text
    assert "issues: write" in text
    assert "pull-requests: write" not in text
    assert "id-token: write" not in text
    assert "workflow_dispatch:\n\npermissions:" in text
    assert "inputs:" not in text


def test_workflow_uses_fixed_reviewed_installer_and_singular_timer() -> None:
    text = _text()
    assert "sudo" not in text
    assert "install_scheduler_core.sh" not in text
    assert 'python3 scripts/install_scheduler_runtime.py --expected-sha "${GITHUB_SHA}" --enable' in text
    assert "SKELETON_SCHEDULER_INSTALL_ROOT" not in text
    assert "SKELETON_SCHEDULER_STATE_ROOT" not in text
    assert "systemctl --user is-enabled skeleton-scheduler.timer" in text
    assert "systemctl --user is-active skeleton-scheduler.timer" in text
    assert "systemctl --user list-timers skeleton-scheduler.timer" in text
    assert 'test "${origin}" = "https://github.com/alanua/Skeleton"' in text


def test_workflow_smoke_is_isolated_idempotent_and_removed() -> None:
    text = _text()
    assert "tests/test_scheduler_runtime_install.py" in text
    assert "scripts/install_scheduler_runtime.py" in text
    assert '"smoke_first_created": 1' in text
    assert '"smoke_first_done": 1' in text
    assert '"smoke_second_created": 0' in text
    assert '"smoke_occurrence_count": 1' in text
    assert '"synthetic_state_removed": True' in text
    assert "/var/lib/skeleton/scheduler/scheduler.sqlite3 register" not in text


def test_workflow_publishes_only_aggregate_receipt_and_fails_closed() -> None:
    text = _text()
    assert "issues/2051/comments" in text
    assert '"public_safe": True' in text
    assert '"timer_enabled"' in text
    assert '"timer_active"' in text
    assert '"smoke_first_created"' in text
    assert '"smoke_first_done"' in text
    assert '"smoke_second_created"' in text
    assert '"stable_reason_codes"' in text
    assert '"workflow_outcomes"' in text
    assert "schedule payload" not in text.casefold()
    assert "steps.install.outcome != 'success'" in text
    assert "steps.verify.outcome != 'success'" in text
    assert "steps.smoke" not in text


def test_workflow_parses_with_pyyaml_baseloader() -> None:
    import yaml

    parsed = yaml.load(_text(), Loader=yaml.BaseLoader)
    assert parsed["permissions"]["contents"] == "read"
    assert parsed["permissions"]["issues"] == "write"
