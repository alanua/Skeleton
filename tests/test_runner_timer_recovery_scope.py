from __future__ import annotations

import subprocess

import core.runner_repository_maintenance_executor as maintenance


def test_runner_timer_recovery_uses_only_fixed_system_scope(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(maintenance, "_run_fixed", fake_run)

    report = maintenance._recover_runner_timer()

    assert report.startswith("DONE:")
    assert "reason=RUNNER_TIMER_ACTIVE" in report
    assert calls == [
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "daemon-reload"],
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/systemctl",
            "reset-failed",
            maintenance.RUNNER_SERVICE,
        ],
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/systemctl",
            "reset-failed",
            maintenance.RUNNER_TIMER,
        ],
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/systemctl",
            "restart",
            maintenance.RUNNER_TIMER,
        ],
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/systemctl",
            "is-enabled",
            "--quiet",
            maintenance.RUNNER_TIMER,
        ],
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/systemctl",
            "is-active",
            "--quiet",
            maintenance.RUNNER_TIMER,
        ],
    ]
    assert all("--user" not in argv for argv in calls)
    assert all(argv[:3] == ["/usr/bin/sudo", "-n", "/usr/bin/systemctl"] for argv in calls)


def test_runner_timer_recovery_fails_closed_without_alternate_scope(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], *, timeout: int = 60, cwd: str | None = None):
        calls.append(argv)
        rc = 1 if "restart" in argv else 0
        return subprocess.CompletedProcess(argv, rc, "", "")

    monkeypatch.setattr(maintenance, "_run_fixed", fake_run)

    report = maintenance._recover_runner_timer()

    assert report.startswith("BLOCKED:")
    assert "reason=RUNNER_TIMER_RECOVERY_FAILED" in report
    assert all("--user" not in argv for argv in calls)
    assert not any("is-enabled" in argv or "is-active" in argv for argv in calls)
