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
ALWAYS_PRESERVE = {3305, 3331, 3334}
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


def _validate_issue_list(data: object) -> set[int]:
    if not isinstance(data, list):
        raise RuntimeError("github_state_malformed")
    result: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            raise RuntimeError("github_state_malformed")
        number = item.get("number")
        state = item.get("state")
        if not isinstance(number, int) or state != "open":
            raise RuntimeError("github_state_malformed")
        result.add(number)
    return result


def _gh_open_page(page: int) -> tuple[set[int], int]:
    if not GH:
        raise RuntimeError("gh_unavailable")
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
    if proc.returncode != 0:
        raise RuntimeError("gh_authority_unavailable")
    data = json.loads(proc.stdout)
    return _validate_issue_list(data), len(data)


def _rest_open_page(page: int) -> tuple[set[int], int]:
    req = urllib.request.Request(
        "https://api.github.com/repos/alanua/Skeleton/issues"
        f"?state=open&per_page=100&page={page}",
        headers={
            "User-Agent": "Skeleton-Runner-Disk-Recovery",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.load(response)
    return _validate_issue_list(data), len(data)


def fetch_all_open_numbers() -> set[int]:
    result: set[int] = set()
    use_gh = GH is not None
    for page in range(1, 51):
        try:
            page_numbers, count = _gh_open_page(page) if use_gh else _rest_open_page(page)
        except Exception:
            if use_gh:
                use_gh = False
                page_numbers, count = _rest_open_page(page)
            else:
                raise
        result |= page_numbers
        if count < 100:
            return result
    raise RuntimeError("github_pagination_unbounded")


def fetch_issue_state(issue_number: int) -> str:
    data: object
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
        else:
            data = None
    else:
        data = None
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
    result: set[Path] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            result.add(Path(line[9:]))
    return result


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


def main() -> int:
    before = shutil.disk_usage("/").free
    audit_lines: list[str] = [f"free_before={before}", f"authority={'gh' if GH else 'public_rest'}"]
    try:
        open_numbers = fetch_all_open_numbers() | fetch_all_open_numbers() | ALWAYS_PRESERVE
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
    preserved_open = preserved_registered = preserved_active = 0
    preserved_dirty = preserved_origin = preserved_unsafe = 0

    for p in ROOT.iterdir():
        match = re.fullmatch(r"issue-(\d+)", p.name)
        if match is None:
            continue
        issue_number = int(match.group(1))
        if issue_number in open_numbers:
            preserved_open += 1
            continue
        if p in registered:
            preserved_registered += 1
            continue
        try:
            if p.is_symlink() or not p.is_dir() or p.resolve().parent != root_real:
                preserved_unsafe += 1
                continue
        except OSError:
            preserved_unsafe += 1
            continue
        if any(is_under(active, p) for active in active_paths):
            preserved_active += 1
            continue
        git_marker = p / ".git"
        if not git_marker.is_dir() or git_marker.is_symlink():
            preserved_unsafe += 1
            continue
        origin = run("git", "-C", str(p), "remote", "get-url", "origin")
        if origin.returncode != 0 or origin.stdout.strip() not in ALLOWED_ORIGINS:
            preserved_origin += 1
            continue
        status = run("git", "-C", str(p), "status", "--porcelain=v1", "--untracked-files=all")
        if status.returncode != 0 or status.stdout.strip():
            preserved_dirty += 1
            continue
        candidates.append((issue_number, p))

    candidates.sort(key=lambda pair: pair[0])
    removed: list[int] = []

    for issue_number, p in candidates:
        if len(removed) >= MAX_REMOVE or shutil.disk_usage("/").free >= TARGET_FREE_BYTES:
            break
        try:
            if fetch_issue_state(issue_number) != "closed":
                preserved_open += 1
                continue
        except Exception:
            audit_lines.append("stop=github_recheck_unavailable")
            break
        try:
            if p.is_symlink() or not p.is_dir() or p.resolve().parent != root_real:
                preserved_unsafe += 1
                continue
        except OSError:
            preserved_unsafe += 1
            continue
        live_paths = active_process_paths()
        if any(is_under(active, p) for active in live_paths):
            preserved_active += 1
            continue
        origin = run("git", "-C", str(p), "remote", "get-url", "origin")
        status = run("git", "-C", str(p), "status", "--porcelain=v1", "--untracked-files=all")
        if origin.returncode != 0 or origin.stdout.strip() not in ALLOWED_ORIGINS:
            preserved_origin += 1
            continue
        if status.returncode != 0 or status.stdout.strip():
            preserved_dirty += 1
            continue
        shutil.rmtree(p)
        removed.append(issue_number)
        audit_lines.append(f"removed_closed_clean={issue_number}")

    after = shutil.disk_usage("/").free
    freed = max(0, after - before)
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    AUDIT_ROOT.chmod(0o700)
    audit = AUDIT_ROOT / ("runner-issue-workspace-cleanup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".log")
    audit_lines.extend([
        f"removed_count={len(removed)}",
        f"preserved_open={preserved_open}",
        f"preserved_registered={preserved_registered}",
        f"preserved_active={preserved_active}",
        f"preserved_dirty={preserved_dirty}",
        f"preserved_origin={preserved_origin}",
        f"preserved_unsafe={preserved_unsafe}",
        f"freed_bytes={freed}",
        f"free_after={after}",
    ])
    audit.write_text("\n".join(audit_lines) + "\n", encoding="utf-8")
    audit.chmod(0o600)
    digest = hashlib.sha256(audit.read_bytes()).hexdigest()
    print("STATUS=OK")
    print(f"REMOVED={len(removed)}")
    print(f"PRESERVED_OPEN={preserved_open}")
    print(f"PRESERVED_ACTIVE={preserved_active}")
    print(f"PRESERVED_DIRTY={preserved_dirty}")
    print(f"PRESERVED_UNSAFE={preserved_registered + preserved_origin + preserved_unsafe}")
    print(f"FREED_GIB={freed / 1024**3:.2f}")
    print(f"FREE_GIB={after / 1024**3:.2f}")
    print(f"AUDIT_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
