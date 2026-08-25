#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request

REPO = Path("/home/agent/agent-dev/repos/Skeleton")
ROOT = Path("/home/agent/agent-dev/worktrees/skeleton")
AUDIT_ROOT = Path("/home/agent/.local/state/skeleton/private-audits")
TARGET_FREE_BYTES = 12 * 1024**3
MAX_REMOVE = 250
ALWAYS_PRESERVE = {3305, 3331, 3334, 3362, 3363, 3364, 3366}
ALLOWED_ORIGINS = {
    "/home/agent/agent-dev/repos/Skeleton",
    "file:///home/agent/agent-dev/repos/Skeleton",
    "https://github.com/alanua/Skeleton.git",
    "git@github.com:alanua/Skeleton.git",
}
GH = shutil.which("gh")


def run(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def _validate_issue_list(data: object) -> tuple[set[int], int]:
    if not isinstance(data, list):
        raise RuntimeError("github_state_malformed")
    numbers: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            raise RuntimeError("github_state_malformed")
        number = item.get("number")
        state = item.get("state")
        if not isinstance(number, int) or state != "open":
            raise RuntimeError("github_state_malformed")
        numbers.add(number)
    return numbers, len(data)


def _fetch_open_page(page: int) -> tuple[set[int], int]:
    if GH:
        proc = run(
            GH,
            "api",
            "--method",
            "GET",
            "/repos/alanua/Skeleton/issues",
            "-f",
            "state=open",
            "-f",
            "per_page=100",
            "-f",
            f"page={page}",
        )
        if proc.returncode == 0:
            return _validate_issue_list(json.loads(proc.stdout))
    req = urllib.request.Request(
        "https://api.github.com/repos/alanua/Skeleton/issues"
        f"?state=open&per_page=100&page={page}",
        headers={
            "User-Agent": "Skeleton-Runner-Disk-Recovery",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return _validate_issue_list(json.load(response))


def fetch_all_open_numbers() -> set[int]:
    result: set[int] = set(ALWAYS_PRESERVE)
    for page in range(1, 51):
        page_numbers, count = _fetch_open_page(page)
        result |= page_numbers
        if count < 100:
            return result
    raise RuntimeError("github_pagination_unbounded")


def fetch_issue_state(issue_number: int) -> str:
    data: object | None = None
    if GH:
        proc = run(
            GH,
            "api",
            "--method",
            "GET",
            f"/repos/alanua/Skeleton/issues/{issue_number}",
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
    if data is None:
        req = urllib.request.Request(
            f"https://api.github.com/repos/alanua/Skeleton/issues/{issue_number}",
            headers={
                "User-Agent": "Skeleton-Runner-Disk-Recovery",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.load(response)
    if not isinstance(data, dict):
        raise RuntimeError("github_issue_malformed")
    if data.get("number") != issue_number or data.get("state") not in {"open", "closed"}:
        raise RuntimeError("github_issue_malformed")
    return str(data["state"])


def registered_worktrees() -> set[Path]:
    proc = run("git", "-C", str(REPO), "worktree", "list", "--porcelain")
    if proc.returncode != 0:
        raise RuntimeError("git_worktree_registry_unavailable")
    return {
        Path(line[9:])
        for line in proc.stdout.splitlines()
        if line.startswith("worktree ")
    }


def active_process_paths() -> set[Path]:
    paths: set[Path] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        links = [entry / "cwd"]
        try:
            links.extend((entry / "fd").iterdir())
        except (FileNotFoundError, PermissionError, NotADirectoryError):
            pass
        for link in links:
            try:
                target = Path(os.readlink(link))
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if target.is_absolute():
                paths.add(target)
    return paths


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_clone_candidate(
    path: Path,
    *,
    root_real: Path,
    registered: set[Path],
    active_paths: set[Path],
) -> bool:
    if path in registered:
        return False
    try:
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != root_real:
            return False
    except OSError:
        return False
    if any(is_under(active, path) for active in active_paths):
        return False
    marker = path / ".git"
    if not marker.is_dir() or marker.is_symlink():
        return False
    origin = run("git", "-C", str(path), "remote", "get-url", "origin")
    if origin.returncode != 0 or origin.stdout.strip() not in ALLOWED_ORIGINS:
        return False
    status = run("git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0 or status.stdout.strip():
        return False
    return True


def main() -> int:
    before = shutil.disk_usage("/").free
    try:
        open_numbers = fetch_all_open_numbers() | fetch_all_open_numbers()
        registered = registered_worktrees()
        active_paths = active_process_paths()
    except Exception:
        print("STATUS=BLOCKED_AUTHORITY")
        print("REMOVED=0")
        print(f"FREE_GIB={before / 1024**3:.2f}")
        return 0

    if not ROOT.exists() or ROOT.is_symlink() or not ROOT.is_dir():
        print("STATUS=BLOCKED_ROOT")
        print("REMOVED=0")
        print(f"FREE_GIB={before / 1024**3:.2f}")
        return 0

    root_real = ROOT.resolve()
    candidates: list[tuple[int, Path]] = []
    preserved_open = preserved_active = preserved_dirty_or_unsafe = 0
    preserved_permission = 0

    for path in ROOT.iterdir():
        match = re.fullmatch(r"issue-(\d+)", path.name)
        if match is None:
            continue
        issue_number = int(match.group(1))
        if issue_number in open_numbers:
            preserved_open += 1
            continue
        if safe_clone_candidate(
            path,
            root_real=root_real,
            registered=registered,
            active_paths=active_paths,
        ):
            candidates.append((issue_number, path))
        else:
            preserved_dirty_or_unsafe += 1

    candidates.sort(key=lambda pair: pair[0])
    removed: list[int] = []
    audit_lines = [f"free_before={before}"]

    for issue_number, path in candidates:
        if len(removed) >= MAX_REMOVE or shutil.disk_usage("/").free >= TARGET_FREE_BYTES:
            break
        try:
            if fetch_issue_state(issue_number) != "closed":
                preserved_open += 1
                continue
        except Exception:
            audit_lines.append("stop=github_recheck_unavailable")
            break

        if not safe_clone_candidate(
            path,
            root_real=root_real,
            registered=registered_worktrees(),
            active_paths=active_process_paths(),
        ):
            preserved_dirty_or_unsafe += 1
            continue

        try:
            shutil.rmtree(path)
        except (PermissionError, OSError) as exc:
            preserved_permission += 1
            audit_lines.append(
                f"preserve_permission_or_delete_error={issue_number}:{type(exc).__name__}"
            )
            continue

        removed.append(issue_number)
        audit_lines.append(f"removed_closed_clean={issue_number}")

    after = shutil.disk_usage("/").free
    freed = max(0, after - before)
    audit_lines.extend(
        [
            f"removed_count={len(removed)}",
            f"preserved_open={preserved_open}",
            f"preserved_dirty_or_unsafe={preserved_dirty_or_unsafe}",
            f"preserved_permission={preserved_permission}",
            f"freed_bytes={freed}",
            f"free_after={after}",
        ]
    )

    digest = "AUDIT_NOT_WRITTEN"
    try:
        AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
        AUDIT_ROOT.chmod(0o700)
        audit = AUDIT_ROOT / (
            "runner-issue-workspace-cleanup-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + ".log"
        )
        audit.write_text("\n".join(audit_lines) + "\n", encoding="utf-8")
        audit.chmod(0o600)
        digest = hashlib.sha256(audit.read_bytes()).hexdigest()
    except OSError:
        pass

    print("STATUS=OK")
    print(f"REMOVED={len(removed)}")
    print(f"PRESERVED_OPEN={preserved_open}")
    print(f"PRESERVED_PERMISSION={preserved_permission}")
    print(f"PRESERVED_OTHER={preserved_dirty_or_unsafe}")
    print(f"FREED_GIB={freed / 1024**3:.2f}")
    print(f"FREE_GIB={after / 1024**3:.2f}")
    print(f"AUDIT_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
