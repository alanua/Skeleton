from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml


DEFAULT_REPOSITORY = "alanua/Skeleton"
DEFAULT_WORKTREE_ROOT = Path("/home/agent/agent-dev/worktrees/skeleton")
DEFAULT_REPORT_PATH = Path("var/host_maintenance_report.json")
QUARANTINE_DIRNAME = ".quarantine"
ALLOWED_COMMANDS = frozenset(
    {
        "worktree_audit",
        "worktree_quarantine_clean_stale",
        "worktree_prune",
        "poller_status",
    }
)
ALLOWED_PACKET_FIELDS = frozenset({"command", "repository", "apply", "stale_days", "candidates"})
PACKET_SUFFIXES = frozenset({".yaml", ".yml", ".json"})
BLOCKED_TEXT = (
    "secret",
    "password",
    "passwd",
    "credential",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "bearer ",
    "authorization:",
    "ssh-rsa",
    "-----BEGIN ",
)
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)


class HostMaintenanceError(ValueError):
    """Raised when a host maintenance packet is outside the bounded contract."""


@dataclass(frozen=True)
class HostMaintenanceReport:
    status: str
    command: str
    repository: str
    apply: bool
    worktree_root: str
    candidates: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    blocked_reason: str | None = None

    def compact(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def process_host_maintenance(
    packet_path: str | Path,
    *,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    worktree_root: str | Path = DEFAULT_WORKTREE_ROOT,
    now: datetime | None = None,
) -> HostMaintenanceReport:
    root = Path(worktree_root)
    report_file = Path(report_path)
    try:
        packet = _load_packet(Path(packet_path))
        command = _require_string(packet, "command")
        repository = packet.get("repository", DEFAULT_REPOSITORY)
        if not isinstance(repository, str) or not repository.strip():
            raise HostMaintenanceError("repository must be a non-empty string")
        if command not in ALLOWED_COMMANDS:
            raise HostMaintenanceError(f"command is not allowlisted: {command}")
        if repository != DEFAULT_REPOSITORY:
            raise HostMaintenanceError(f"repository is not allowlisted: {repository}")
        apply = _optional_bool(packet, "apply", False)
        stale_days = _optional_int(packet, "stale_days", 14)
        if stale_days < 0:
            raise HostMaintenanceError("stale_days must be non-negative")
        candidates = _candidate_paths(packet, root)
        report = _execute_command(
            command,
            repository,
            apply,
            root,
            candidates,
            stale_days=stale_days,
            now=now or datetime.now(UTC),
        )
    except HostMaintenanceError as exc:
        report = HostMaintenanceReport(
            status="blocked",
            command=str(locals().get("command", "<unreadable>")),
            repository=str(locals().get("repository", DEFAULT_REPOSITORY)),
            apply=bool(locals().get("apply", False)),
            worktree_root=str(root),
            candidates=[],
            actions=[],
            blocked_reason=str(exc),
        )

    _write_report(report_file, report.compact())
    return report


def _load_packet(packet_path: Path) -> dict[str, Any]:
    if packet_path.suffix.lower() not in PACKET_SUFFIXES:
        raise HostMaintenanceError("packet must be YAML or JSON")
    with packet_path.open("r", encoding="utf-8") as handle:
        packet = yaml.safe_load(handle)
    if not isinstance(packet, dict):
        raise HostMaintenanceError("packet must be a mapping")
    extra = set(packet) - ALLOWED_PACKET_FIELDS
    if extra:
        raise HostMaintenanceError(f"packet has non-allowlisted fields: {', '.join(sorted(extra))}")
    _check_no_secret_like_content(packet)
    return packet


def _require_string(packet: dict[str, Any], key: str) -> str:
    value = packet.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HostMaintenanceError(f"{key} must be a non-empty string")
    return value


def _optional_bool(packet: dict[str, Any], key: str, default: bool) -> bool:
    value = packet.get(key, default)
    if not isinstance(value, bool):
        raise HostMaintenanceError(f"{key} must be a boolean")
    return value


def _optional_int(packet: dict[str, Any], key: str, default: int) -> int:
    value = packet.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise HostMaintenanceError(f"{key} must be an integer")
    return value


def _candidate_paths(packet: dict[str, Any], worktree_root: Path) -> list[Path]:
    raw_candidates = packet.get("candidates")
    if raw_candidates is None:
        if not worktree_root.exists():
            return []
        return sorted(
            path
            for path in worktree_root.iterdir()
            if path.is_dir() and path.name.startswith("issue-")
        )
    if not isinstance(raw_candidates, list):
        raise HostMaintenanceError("candidates must be a list when provided")

    candidates: list[Path] = []
    seen: set[Path] = set()
    for index, raw_candidate in enumerate(raw_candidates):
        if not isinstance(raw_candidate, str) or not raw_candidate.strip():
            raise HostMaintenanceError(f"candidate {index} must be a non-empty string")
        candidate = _resolve_candidate(raw_candidate, worktree_root)
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
    return sorted(candidates)


def _resolve_candidate(raw_candidate: str, worktree_root: Path) -> Path:
    root = worktree_root.resolve()
    candidate = Path(raw_candidate)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HostMaintenanceError(f"candidate path is outside allowlist: {raw_candidate}") from exc
    if resolved.parent != root or not resolved.name.startswith("issue-"):
        raise HostMaintenanceError(f"candidate path is not an issue worktree: {raw_candidate}")
    if resolved.name.startswith("validate-pr-branch"):
        raise HostMaintenanceError(f"candidate path is blocked: {raw_candidate}")
    return resolved


def _execute_command(
    command: str,
    repository: str,
    apply: bool,
    worktree_root: Path,
    candidates: list[Path],
    *,
    stale_days: int,
    now: datetime,
) -> HostMaintenanceReport:
    if command == "poller_status":
        return HostMaintenanceReport(
            status="ok",
            command=command,
            repository=repository,
            apply=apply,
            worktree_root=str(worktree_root),
            candidates=[],
            actions=[{"action": "poller_status", "status": "not_configured"}],
        )

    inspected = [_inspect_candidate(path, repository, stale_days=stale_days, now=now) for path in candidates]
    if command == "worktree_audit":
        return HostMaintenanceReport("ok", command, repository, apply, str(worktree_root), inspected, [])

    if command == "worktree_prune":
        return HostMaintenanceReport(
            "ok",
            command,
            repository,
            apply,
            str(worktree_root),
            inspected,
            [{"action": "worktree_prune", "status": "dry_run_only"}],
        )

    actions = _quarantine_actions(worktree_root, inspected, apply)
    return HostMaintenanceReport("ok", command, repository, apply, str(worktree_root), inspected, actions)


def _inspect_candidate(
    path: Path,
    repository: str,
    *,
    stale_days: int,
    now: datetime,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "eligible": False,
        "skip_reason": None,
    }
    if not path.exists():
        info["skip_reason"] = "missing"
        return _without_none(info)
    if not (path / ".git").exists():
        info["skip_reason"] = "not_git_checkout"
        return _without_none(info)

    remote = _git_output(path, ["remote", "get-url", "origin"])
    if remote.returncode != 0:
        info["skip_reason"] = "origin_unreadable"
        return _without_none(info)
    origin = remote.stdout.strip()
    info["origin"] = origin
    if not _origin_matches(origin, repository):
        info["skip_reason"] = "wrong_remote"
        return _without_none(info)

    status = _git_output(path, ["status", "--porcelain"])
    if status.returncode != 0:
        info["skip_reason"] = "status_unreadable"
        return _without_none(info)
    if status.stdout.strip():
        info["dirty"] = True
        info["skip_reason"] = "dirty"
        return _without_none(info)
    info["dirty"] = False

    cutoff = now - timedelta(days=stale_days)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    info["stale"] = mtime <= cutoff
    if not info["stale"]:
        info["skip_reason"] = "not_stale"
        return _without_none(info)

    info["eligible"] = True
    return _without_none(info)


def _git_output(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _origin_matches(origin: str, repository: str) -> bool:
    normalized = origin.strip()
    return normalized in {
        f"https://github.com/{repository}.git",
        f"https://github.com/{repository}",
        f"git@github.com:{repository}.git",
    }


def _quarantine_actions(worktree_root: Path, candidates: list[dict[str, Any]], apply: bool) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    quarantine_root = worktree_root / QUARANTINE_DIRNAME
    for candidate in candidates:
        if not candidate.get("eligible"):
            continue
        source = Path(candidate["path"])
        destination = _unique_destination(quarantine_root / source.name)
        action = {
            "action": "quarantine",
            "source": str(source),
            "destination": str(destination),
            "status": "planned",
        }
        if apply:
            quarantine_root.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            action["status"] = "applied"
        actions.append(action)
    return actions


def _unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    for index in range(1, 1000):
        candidate = destination.with_name(f"{destination.name}-{index}")
        if not candidate.exists():
            return candidate
    raise HostMaintenanceError(f"could not allocate quarantine destination for {destination.name}")


def _without_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if child is not None}


def _write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _check_no_secret_like_content(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                _check_secret_text(key)
            _check_no_secret_like_content(child)
    elif isinstance(value, list):
        for child in value:
            _check_no_secret_like_content(child)
    elif isinstance(value, str):
        _check_secret_text(value)


def _check_secret_text(text: str) -> None:
    lowered = text.lower()
    if any(blocked in lowered for blocked in BLOCKED_TEXT):
        raise HostMaintenanceError("packet contains secret-like text")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise HostMaintenanceError("packet contains secret-like text")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded host maintenance packet.")
    parser.add_argument("packet", help="YAML or JSON host maintenance packet.")
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH), help="JSON report path.")
    parser.add_argument("--worktree-root", default=str(DEFAULT_WORKTREE_ROOT), help="Skeleton issue worktree root.")
    args = parser.parse_args(argv)

    report = process_host_maintenance(args.packet, report_path=args.report_path, worktree_root=args.worktree_root)
    print(json.dumps(report.compact(), sort_keys=True, separators=(",", ":")))
    return 0 if report.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
