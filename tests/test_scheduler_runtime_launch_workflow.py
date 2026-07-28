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
    assert "sudo -n env" in text
    assert 'bash scripts/install_scheduler_core.sh "${GITHUB_WORKSPACE}"' in text
    assert "SKELETON_SCHEDULER_INSTALL_ROOT=/opt/skeleton-scheduler" in text
    assert "SKELETON_SCHEDULER_STATE_ROOT=/var/lib/skeleton/scheduler" in text
    assert "systemctl is-enabled skeleton-scheduler.timer" in text
    assert "systemctl is-active skeleton-scheduler.timer" in text
    assert "systemctl list-timers skeleton-scheduler.timer" in text
    assert 'test "${origin}" = "https://github.com/alanua/Skeleton"' in text


def test_workflow_smoke_is_isolated_idempotent_and_removed() -> None:
    text = _text()
    assert 'mktemp -d "${RUNNER_TEMP}/scheduler-smoke.XXXXXX"' in text
    assert "synthetic.launch.smoke" in text
    assert 'trap \'rm -rf "${smoke_root}"\' EXIT' in text
    assert 'first.get("created_occurrences") != 1' in text
    assert 'second.get("created_occurrences") != 0' in text
    assert 'status.get("schedule_occurrences") != 1' in text
    assert '"synthetic_state_removed": True' in text
    assert "/var/lib/skeleton/scheduler/scheduler.sqlite3 register" not in text


def test_workflow_publishes_only_aggregate_receipt_and_fails_closed() -> None:
    text = _text()
    assert "issues/2051/comments" in text
    assert '"public_safe": True' in text
    assert 'payload.get("private_payloads_included") is not False' in text
    assert '"timer_enabled"' in text
    assert '"timer_active"' in text
    assert '"smoke_first_created"' in text
    assert '"smoke_second_created"' in text
    assert '"stable_reason_codes"' in text
    assert "schedule payload" not in text.casefold()
    assert "steps.install.outcome != 'success'" in text
    assert "steps.verify.outcome != 'success'" in text
    assert "steps.smoke.outcome != 'success'" in text


def test_workflow_receipt_verification_rejects_bool_int_confusion() -> None:
    text = _text()
    assert "def exact_bool(payload, field, expected=True):" in text
    assert "payload.get(field) is expected" in text
    assert "def exact_int(payload, field, expected):" in text
    assert "not isinstance(value, bool)" in text
    assert 'exact_int(verify, "timer_count", 1)' in text
    assert 'exact_int(smoke, "first_created", 1)' in text
    assert 'exact_bool(verify, "timer_enabled")' in text
    assert 'exact_bool(smoke, "synthetic_state_removed")' in text
    assert 'verify.get("timer_count") == 1' not in text
    assert 'smoke.get("first_created") == 1' not in text
