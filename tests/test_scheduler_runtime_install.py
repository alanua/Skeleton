from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.scheduler_runtime_install import (
    RECEIPT_SCHEMA,
    SERVICE_UNIT,
    TIMER_UNIT,
    SchedulerRuntimeInstallError,
    SchedulerRuntimeInstallResult,
    _install_units,
    _rollback,
    _run_synthetic_smoke,
    _verify_source,
    failure_receipt,
    install_scheduler_runtime,
    scheduler_runtime_layout,
)
from scripts import install_scheduler_runtime as cli


SHA = "b" * 40
PUBLIC_FIELDS = {
    "schema",
    "source_merge_sha",
    "runtime_status",
    "service_install_status",
    "timer_enabled",
    "timer_active",
    "timer_count",
    "service_result",
    "live_status",
    "smoke_first_created",
    "smoke_first_done",
    "smoke_second_created",
    "smoke_occurrence_count",
    "synthetic_state_removed",
    "rollback_ready",
    "stable_reason_codes",
}


def test_runtime_layout_stays_under_user_roots(tmp_path: Path) -> None:
    layout = scheduler_runtime_layout(tmp_path)
    assert layout.base.is_relative_to(tmp_path / ".local" / "share")
    assert layout.releases.is_relative_to(tmp_path / ".local" / "share")
    assert layout.state.is_relative_to(tmp_path / ".local" / "state")
    assert layout.live_db == tmp_path / ".local/state/skeleton/scheduler/scheduler.sqlite3"
    assert layout.systemd_dir == tmp_path / ".config/systemd/user"


def test_public_receipt_has_exact_aggregate_fields() -> None:
    result = SchedulerRuntimeInstallResult(
        source_sha=SHA,
        runtime_status="READY",
        service_install_status="ACTIVE",
        timer_enabled=True,
        timer_active=True,
        timer_count=1,
        service_result="success",
        live_status="READY",
        smoke_first_created=1,
        smoke_first_done=1,
        smoke_second_created=0,
        smoke_occurrence_count=1,
        synthetic_state_removed=True,
        rollback_ready=True,
        stable_reason_codes=(),
    )
    payload = result.public_dict()
    assert set(payload) == PUBLIC_FIELDS
    assert payload["schema"] == RECEIPT_SCHEMA
    assert "path" not in json.dumps(payload).casefold()
    assert "sqlite" not in json.dumps(payload).casefold()


def test_verify_source_requires_exact_sha_clean_origin(monkeypatch, tmp_path: Path) -> None:
    values = {
        ("rev-parse", "HEAD"): SHA,
        ("status", "--porcelain"): "",
        ("remote", "get-url", "origin"): "https://github.com/alanua/Skeleton.git",
    }
    monkeypatch.setattr(
        "core.scheduler_runtime_install._git",
        lambda source_root, *args: values[args],
    )
    assert _verify_source(tmp_path, SHA) == SHA

    with pytest.raises(SchedulerRuntimeInstallError) as exc:
        _verify_source(tmp_path, "not-a-sha")
    assert exc.value.reason_code == "EXPECTED_SHA_INVALID"

    values[("remote", "get-url", "origin")] = "https://github.com/other/Skeleton.git"
    with pytest.raises(SchedulerRuntimeInstallError) as exc:
        _verify_source(tmp_path, SHA)
    assert exc.value.reason_code == "SOURCE_ORIGIN_MISMATCH"


def test_units_are_user_level_fixed_oneshot_timer(tmp_path: Path) -> None:
    layout = scheduler_runtime_layout(tmp_path)
    layout.systemd_dir.mkdir(parents=True)
    release = tmp_path / "release"
    (release / "scripts").mkdir(parents=True)
    _install_units(layout, release=release, source_sha=SHA)
    service = (layout.systemd_dir / SERVICE_UNIT).read_text(encoding="utf-8")
    timer = (layout.systemd_dir / TIMER_UNIT).read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert f"{release / 'scripts' / 'scheduler_tick.py'} --db {layout.live_db} tick" in service
    assert "Environment=" not in service
    assert "ExecStart=/bin/sh" not in service
    assert "ExecStart=/bin/bash" not in service
    assert "OnUnitActiveSec=60" in timer
    assert "Persistent=true" in timer
    assert f"Unit={SERVICE_UNIT}" in timer


def test_synthetic_smoke_is_idempotent_and_removed(tmp_path: Path) -> None:
    result = _run_synthetic_smoke(tmp_path)
    assert result.first_created == 1
    assert result.first_done == 1
    assert result.second_created == 0
    assert result.occurrence_count == 1
    assert result.state_removed is True
    assert not list(tmp_path.glob("scheduler-smoke.*"))


def test_rollback_restores_previous_symlink_and_units(tmp_path: Path) -> None:
    layout = scheduler_runtime_layout(tmp_path)
    layout.systemd_dir.mkdir(parents=True)
    old_release = tmp_path / "old"
    new_release = tmp_path / "new"
    old_release.mkdir()
    new_release.mkdir()
    (layout.base).mkdir(parents=True)
    (layout.systemd_dir / SERVICE_UNIT).write_text("new-service", encoding="utf-8")
    (layout.systemd_dir / TIMER_UNIT).write_text("new-timer", encoding="utf-8")
    layout.current.symlink_to(new_release)
    _rollback(
        layout,
        previous_target=old_release,
        previous_service="old-service",
        previous_timer="old-timer",
    )
    assert layout.current.resolve() == old_release
    assert (layout.systemd_dir / SERVICE_UNIT).read_text(encoding="utf-8") == "old-service"
    assert (layout.systemd_dir / TIMER_UNIT).read_text(encoding="utf-8") == "old-timer"


def test_install_rolls_back_units_on_failure(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    home = tmp_path / "home"
    layout = scheduler_runtime_layout(home)
    layout.systemd_dir.mkdir(parents=True)
    old_release = tmp_path / "old-release"
    old_release.mkdir()
    layout.base.mkdir(parents=True)
    layout.current.symlink_to(old_release)
    (layout.systemd_dir / SERVICE_UNIT).write_text("old-service", encoding="utf-8")
    (layout.systemd_dir / TIMER_UNIT).write_text("old-timer", encoding="utf-8")
    monkeypatch.setattr("core.scheduler_runtime_install._verify_source", lambda *args: SHA)
    monkeypatch.setattr("core.scheduler_runtime_install._install_release", lambda *args: None)

    def fail_units(*args: Any, **kwargs: Any) -> None:
        (layout.systemd_dir / SERVICE_UNIT).write_text("new-service", encoding="utf-8")
        raise SchedulerRuntimeInstallError("UNIT_WRITE_FAILED")

    monkeypatch.setattr("core.scheduler_runtime_install._install_units", fail_units)
    with pytest.raises(SchedulerRuntimeInstallError):
        install_scheduler_runtime(source, expected_sha=SHA, enable=False, home=home)
    assert layout.current.resolve() == old_release
    assert (layout.systemd_dir / SERVICE_UNIT).read_text(encoding="utf-8") == "old-service"
    assert (layout.systemd_dir / TIMER_UNIT).read_text(encoding="utf-8") == "old-timer"


def test_enable_uses_user_systemctl_and_verifies_singular_timer(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("core.scheduler_runtime_install._verify_source", lambda *args: SHA)
    monkeypatch.setattr("core.scheduler_runtime_install._install_release", lambda *args: None)
    monkeypatch.setattr("core.scheduler_runtime_install._install_units", lambda *args, **kwargs: None)

    def systemctl(*args: str):
        calls.append(args)
        class Completed:
            returncode = 0
            stdout = ""
            stderr = ""
        if args == ("is-enabled", TIMER_UNIT):
            Completed.stdout = "enabled\n"
        elif args == ("is-active", TIMER_UNIT):
            Completed.stdout = "active\n"
        elif args == ("list-timers", TIMER_UNIT, "--all", "--no-legend"):
            Completed.stdout = "NEXT LEFT LAST PASSED UNIT ACTIVATES\n"
        elif args == ("show", SERVICE_UNIT, "--property=Result", "--value"):
            Completed.stdout = "success\n"
        return Completed()

    monkeypatch.setattr("core.scheduler_runtime_install._systemctl_user", systemctl)
    result = install_scheduler_runtime(source, expected_sha=SHA, enable=True, home=tmp_path / "home")
    assert result.timer_enabled is True
    assert result.timer_active is True
    assert result.timer_count == 1
    assert result.service_result == "success"
    assert ("enable", "--now", TIMER_UNIT) in calls
    assert ("start", SERVICE_UNIT) in calls


def test_failure_receipt_is_stable_and_contains_no_private_values(tmp_path: Path) -> None:
    payload = failure_receipt(str(tmp_path), "bad reason with spaces")
    assert set(payload) == PUBLIC_FIELDS
    assert payload["runtime_status"] == "BLOCKED"
    assert payload["source_merge_sha"] == "0" * 40
    assert payload["stable_reason_codes"] == ["SCHEDULER_RUNTIME_FAILED"]
    assert str(tmp_path) not in json.dumps(payload)


def test_cli_prints_sorted_blocked_receipt(monkeypatch, capsys) -> None:
    def fail(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        raise SchedulerRuntimeInstallError("SOURCE_CHECKOUT_DIRTY")

    monkeypatch.setattr(cli, "install_scheduler_runtime", fail)
    assert cli.main(["--expected-sha", SHA, "--enable"]) == 2
    output = capsys.readouterr().out.strip()
    assert output == json.dumps(json.loads(output), sort_keys=True)
    payload = json.loads(output)
    assert set(payload) == PUBLIC_FIELDS
    assert payload["stable_reason_codes"] == ["SOURCE_CHECKOUT_DIRTY"]
