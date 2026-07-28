from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from core.scheduler_engine import SchedulerEngine
from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore


RECEIPT_SCHEMA = "skeleton.scheduler.runtime_install_receipt.v1"
SERVICE_UNIT = "skeleton-scheduler.service"
TIMER_UNIT = "skeleton-scheduler.timer"
LIVE_DB_NAME = "scheduler.sqlite3"
CANONICAL_ORIGIN = "https://github.com/alanua/Skeleton"
RELEASE_FILE_ALLOWLIST = (
    "core/__init__.py",
    "core/scheduler_models.py",
    "core/scheduler_store.py",
    "core/scheduler_engine.py",
    "scripts/scheduler_tick.py",
    "schemas/schedule.schema.json",
    "schemas/scheduler_receipt.schema.json",
)
_SAFE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REASON_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


class SchedulerRuntimeInstallError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SchedulerRuntimeLayout:
    base: Path
    releases: Path
    current: Path
    state: Path
    live_db: Path
    systemd_dir: Path


@dataclass(frozen=True)
class _PriorTimerState:
    symlink_target: Path | None
    service_unit: str | None
    timer_unit: str | None
    enabled: bool
    active: bool
    captured: bool


@dataclass(frozen=True)
class SchedulerRuntimeInstallResult:
    source_sha: str
    runtime_status: str
    service_install_status: str
    timer_enabled: bool
    timer_active: bool
    timer_count: int
    service_result: str
    live_status: str
    smoke_first_created: int
    smoke_first_done: int
    smoke_second_created: int
    smoke_occurrence_count: int
    synthetic_state_removed: bool
    rollback_ready: bool
    stable_reason_codes: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "schema": RECEIPT_SCHEMA,
            "source_merge_sha": self.source_sha,
            "runtime_status": self.runtime_status,
            "service_install_status": self.service_install_status,
            "timer_enabled": self.timer_enabled,
            "timer_active": self.timer_active,
            "timer_count": self.timer_count,
            "service_result": self.service_result,
            "live_status": self.live_status,
            "smoke_first_created": self.smoke_first_created,
            "smoke_first_done": self.smoke_first_done,
            "smoke_second_created": self.smoke_second_created,
            "smoke_occurrence_count": self.smoke_occurrence_count,
            "synthetic_state_removed": self.synthetic_state_removed,
            "rollback_ready": self.rollback_ready,
            "stable_reason_codes": list(self.stable_reason_codes),
        }


def install_scheduler_runtime(
    source_root: Path,
    *,
    expected_sha: str,
    enable: bool,
    home: Path | None = None,
) -> SchedulerRuntimeInstallResult:
    source_root = source_root.expanduser().resolve(strict=True)
    source_sha = _verify_source(source_root, expected_sha)
    layout = scheduler_runtime_layout(home or Path.home())
    _prepare_layout(layout)
    prior = _capture_prior_state(layout)
    release = layout.releases / source_sha

    try:
        _install_release(source_root, release)
        _atomic_symlink(release, layout.current)
        _install_units(layout, release=release, source_sha=source_sha)
        live_status = _initialize_live_db(layout.live_db)
        if live_status != "READY":
            raise SchedulerRuntimeInstallError("LIVE_STATUS_BLOCKED")
        smoke = _run_synthetic_smoke(layout.state)
        timer_enabled = False
        timer_active = False
        timer_count = 0
        service_result = "not-run"
        service_install_status = "INSTALLED_DISABLED"
        if enable:
            _systemctl_user("daemon-reload")
            _systemctl_user("enable", "--now", TIMER_UNIT)
            timer_enabled = _systemctl_stdout("is-enabled", TIMER_UNIT) == "enabled"
            timer_active = _systemctl_stdout("is-active", TIMER_UNIT) == "active"
            timer_count = _timer_count()
            _systemctl_user("start", SERVICE_UNIT)
            service_result = _systemctl_stdout(
                "show", SERVICE_UNIT, "--property=Result", "--value"
            )
            if (
                not timer_enabled
                or not timer_active
                or timer_count != 1
                or service_result != "success"
            ):
                raise SchedulerRuntimeInstallError("USER_TIMER_VERIFY_FAILED")
            service_install_status = "ACTIVE"
        return SchedulerRuntimeInstallResult(
            source_sha=source_sha,
            runtime_status="READY",
            service_install_status=service_install_status,
            timer_enabled=timer_enabled,
            timer_active=timer_active,
            timer_count=timer_count,
            service_result=service_result,
            live_status=live_status,
            smoke_first_created=smoke.first_created,
            smoke_first_done=smoke.first_done,
            smoke_second_created=smoke.second_created,
            smoke_occurrence_count=smoke.occurrence_count,
            synthetic_state_removed=smoke.state_removed,
            rollback_ready=prior.captured,
            stable_reason_codes=(),
        )
    except Exception:
        _rollback(layout, prior)
        raise


def scheduler_runtime_layout(home: Path) -> SchedulerRuntimeLayout:
    home = home.expanduser().resolve(strict=False)
    base = home / ".local" / "share" / "skeleton" / "scheduler"
    state = home / ".local" / "state" / "skeleton" / "scheduler"
    return SchedulerRuntimeLayout(
        base=base,
        releases=base / "releases",
        current=base / "current",
        state=state,
        live_db=state / LIVE_DB_NAME,
        systemd_dir=home / ".config" / "systemd" / "user",
    )


@dataclass(frozen=True)
class _SmokeResult:
    first_created: int
    first_done: int
    second_created: int
    occurrence_count: int
    state_removed: bool


def _verify_source(source_root: Path, expected_sha: str) -> str:
    if _SAFE_SHA_RE.fullmatch(expected_sha) is None:
        raise SchedulerRuntimeInstallError("EXPECTED_SHA_INVALID")
    actual = _git(source_root, "rev-parse", "HEAD")
    if actual != expected_sha:
        raise SchedulerRuntimeInstallError("SOURCE_SHA_MISMATCH")
    status = _git(source_root, "status", "--porcelain")
    if status:
        raise SchedulerRuntimeInstallError("SOURCE_CHECKOUT_DIRTY")
    origin = _git(source_root, "remote", "get-url", "origin")
    if origin.removesuffix(".git").rstrip("/") != CANONICAL_ORIGIN:
        raise SchedulerRuntimeInstallError("SOURCE_ORIGIN_MISMATCH")
    return actual


def _git(source_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > 8192:
        raise SchedulerRuntimeInstallError("SOURCE_GIT_READ_FAILED")
    return completed.stdout.strip()


def _prepare_layout(layout: SchedulerRuntimeLayout) -> None:
    for path in (layout.base, layout.releases, layout.state, layout.systemd_dir):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)


def _install_release(source_root: Path, release: Path) -> None:
    if release.exists():
        if release.is_dir() and (release / ".source-ready").is_file():
            return
        if release.is_dir():
            shutil.rmtree(release)
        else:
            release.unlink()
    temporary = release.with_name(release.name + ".part")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True, mode=0o700)
    try:
        for relative in RELEASE_FILE_ALLOWLIST:
            source = source_root / relative
            try:
                source_mode = source.lstat().st_mode
            except FileNotFoundError:
                raise SchedulerRuntimeInstallError("RELEASE_SOURCE_MISSING") from None
            if not stat.S_ISREG(source_mode):
                raise SchedulerRuntimeInstallError("RELEASE_SOURCE_MISSING")
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(source, target)
            if relative == "scripts/scheduler_tick.py":
                target.chmod(stat.S_IMODE(source.stat().st_mode) | 0o111)
        (temporary / ".source-ready").write_text("ready\n", encoding="utf-8")
        os.replace(temporary, release)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _install_units(
    layout: SchedulerRuntimeLayout, *, release: Path, source_sha: str
) -> None:
    python = Path(sys.executable).resolve(strict=True)
    cli = release / "scripts" / "scheduler_tick.py"
    service = f"""[Unit]
Description=Skeleton Scheduler Core Tick

[Service]
Type=oneshot
WorkingDirectory={release}
ExecStart={python} {cli} --db {layout.live_db} tick
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

# Source={source_sha}
"""
    timer = f"""[Unit]
Description=Skeleton Scheduler Core 60 Second Timer

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=1s
Persistent=true
Unit={SERVICE_UNIT}

[Install]
WantedBy=timers.target
"""
    _atomic_private_text(layout.systemd_dir / SERVICE_UNIT, service)
    _atomic_private_text(layout.systemd_dir / TIMER_UNIT, timer)


def _initialize_live_db(db_path: Path) -> str:
    store = SchedulerStore(db_path)
    store.initialize()
    counts = store.status_counts()
    required = {
        "schedules",
        "enabled",
        "done",
        "failed",
        "needs_operator",
        "pending",
        "running",
        "skipped",
    }
    if set(counts) != required or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in counts.values()
    ):
        raise SchedulerRuntimeInstallError("LIVE_STATUS_BLOCKED")
    return "READY"


def _run_synthetic_smoke(state_root: Path) -> _SmokeResult:
    smoke_root = Path(tempfile.mkdtemp(prefix="scheduler-smoke.", dir=state_root))
    try:
        db_path = smoke_root / LIVE_DB_NAME
        store = SchedulerStore(db_path)
        store.initialize()
        now = int(time.time())
        spec = ScheduleSpec.from_mapping(
            {
                "schema": "skeleton.schedule.v1",
                "schedule_id": "synthetic.launch.smoke",
                "trigger_kind": "once",
                "cron_expression": None,
                "once_at": now,
                "timezone": "UTC",
                "route_type": "notify",
                "route_id": "synthetic.launch.notice",
                "approval_policy": "notify_only",
                "overlap_policy": "skip",
                "misfire_policy": "run_once",
                "payload": {"synthetic": True},
            }
        )
        store.register(spec, now=max(0, now - 60), enabled=True)
        first = SchedulerEngine(store).tick(now=now)
        second = SchedulerEngine(store).tick(now=now)
        count = store.occurrence_count("synthetic.launch.smoke")
        first_created = _receipt_int(first, "created_occurrences")
        first_done = _state_int(first, "done")
        second_created = _receipt_int(second, "created_occurrences")
        if first_created != 1 or first_done != 1 or second_created != 0 or count != 1:
            raise SchedulerRuntimeInstallError("SYNTHETIC_SMOKE_FAILED")
        return _SmokeResult(first_created, first_done, second_created, count, True)
    finally:
        shutil.rmtree(smoke_root, ignore_errors=True)
        if smoke_root.exists():
            raise SchedulerRuntimeInstallError("SYNTHETIC_STATE_REMOVE_FAILED")


def _receipt_int(payload: Mapping[str, object], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchedulerRuntimeInstallError("SYNTHETIC_SMOKE_FAILED")
    return value


def _state_int(payload: Mapping[str, object], state: str) -> int:
    states = payload.get("states")
    if not isinstance(states, Mapping):
        raise SchedulerRuntimeInstallError("SYNTHETIC_SMOKE_FAILED")
    return _receipt_int(states, state)


def _capture_prior_state(layout: SchedulerRuntimeLayout) -> _PriorTimerState:
    enabled = _systemctl_state("is-enabled", TIMER_UNIT) == "enabled"
    active = _systemctl_state("is-active", TIMER_UNIT) == "active"
    return _PriorTimerState(
        symlink_target=_symlink_target(layout.current),
        service_unit=_read_optional(layout.systemd_dir / SERVICE_UNIT),
        timer_unit=_read_optional(layout.systemd_dir / TIMER_UNIT),
        enabled=enabled,
        active=active,
        captured=True,
    )


def _rollback(layout: SchedulerRuntimeLayout, prior: _PriorTimerState) -> None:
    try:
        _systemctl_user_best_effort("stop", TIMER_UNIT)
        _systemctl_user_best_effort("disable", TIMER_UNIT)
        if prior.symlink_target is None:
            layout.current.unlink(missing_ok=True)
        else:
            _atomic_symlink(prior.symlink_target, layout.current)
        _restore_text(layout.systemd_dir / SERVICE_UNIT, prior.service_unit)
        _restore_text(layout.systemd_dir / TIMER_UNIT, prior.timer_unit)
        _systemctl_user("daemon-reload")
        if prior.enabled and prior.active:
            _systemctl_user("enable", "--now", TIMER_UNIT)
        elif prior.enabled:
            _systemctl_user("enable", TIMER_UNIT)
        elif prior.active:
            _systemctl_user("start", TIMER_UNIT)
    except Exception as exc:
        reason = getattr(exc, "reason_code", "ROLLBACK_FAILED")
        if not isinstance(reason, str) or _SAFE_REASON_RE.fullmatch(reason) is None:
            reason = "ROLLBACK_FAILED"
        raise SchedulerRuntimeInstallError(f"ROLLBACK_FAILED:{reason}") from exc


def _restore_text(path: Path, value: str | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_private_text(path, value)


def _systemctl_user(*args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["systemctl", "--user", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise SchedulerRuntimeInstallError("USER_SERVICE_CONTROL_FAILED")
    return completed


def _systemctl_user_best_effort(*args: str) -> None:
    try:
        _systemctl_user(*args)
    except SchedulerRuntimeInstallError:
        pass


def _systemctl_stdout(*args: str) -> str:
    return _systemctl_user(*args).stdout.strip()


def _systemctl_state(*args: str) -> str:
    completed = subprocess.run(
        ["systemctl", "--user", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode not in {0, 1, 3, 4}:
        raise SchedulerRuntimeInstallError("USER_TIMER_STATE_CAPTURE_FAILED")
    return completed.stdout.strip()


def _timer_count() -> int:
    output = _systemctl_stdout("list-timers", TIMER_UNIT, "--all", "--no-legend")
    return sum(1 for line in output.splitlines() if line.strip())


def _symlink_target(path: Path) -> Path | None:
    if path.is_symlink():
        return Path(os.readlink(path))
    return None


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _atomic_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _atomic_symlink(target: Path, link: Path) -> None:
    temporary = link.with_name(link.name + ".part")
    temporary.unlink(missing_ok=True)
    os.symlink(target, temporary)
    os.replace(temporary, link)


def failure_receipt(expected_sha: str, reason_code: str) -> dict[str, object]:
    reason = (
        reason_code
        if _SAFE_REASON_RE.fullmatch(reason_code)
        else "SCHEDULER_RUNTIME_FAILED"
    )
    source_sha = expected_sha if _SAFE_SHA_RE.fullmatch(expected_sha) else "0" * 40
    return SchedulerRuntimeInstallResult(
        source_sha=source_sha,
        runtime_status="BLOCKED",
        service_install_status="BLOCKED",
        timer_enabled=False,
        timer_active=False,
        timer_count=0,
        service_result="blocked",
        live_status="BLOCKED",
        smoke_first_created=0,
        smoke_first_done=0,
        smoke_second_created=0,
        smoke_occurrence_count=0,
        synthetic_state_removed=False,
        rollback_ready=False,
        stable_reason_codes=(reason,),
    ).public_dict()
