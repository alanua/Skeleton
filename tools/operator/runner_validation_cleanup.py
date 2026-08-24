#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import urllib.request

REPO = Path("/home/agent/agent-dev/repos/Skeleton")
ROOT = Path("/home/agent/agent-dev/worktrees/skeleton/validate-pr-branch")
TARGET_FREE = 12 * 1024**3
MAX_REMOVE = 120
ALWAYS_PRESERVE = {3329}
AUDIT_DIR = Path("/home/agent/.local/state/skeleton/private-audits")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fetch_open_prs() -> set[int]:
    result: set[int] = set()
    for page in range(1, 21):
        req = urllib.request.Request(
            "https://api.github.com/repos/alanua/Skeleton/pulls"
            f"?state=open&per_page=100&page={page}",
            headers={
                "User-Agent": "Skeleton-Runner-Cleanup",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.load(response)
        if not isinstance(data, list):
            raise RuntimeError("BAD_GITHUB_RESPONSE")
        for item in data:
            if not isinstance(item, dict) or item.get("state") != "open" or not isinstance(item.get("number"), int):
                raise RuntimeError("BAD_PR_STATE")
            result.add(item["number"])
        if len(data) < 100:
            return result
    raise RuntimeError("GITHUB_PAGINATION_OVERFLOW")


def finish(status: str, removed: int, preserved: int, skipped: int, before: int, after: int, audit_sha: str = "") -> None:
    print(f"STATUS={status}")
    print(f"REMOVED={removed}")
    print(f"PRESERVED_OPEN={preserved}")
    print(f"SKIPPED={skipped}")
    print(f"FREED_GIB={max(0, after-before)/1024**3:.2f}")
    print(f"FREE_GIB={after/1024**3:.2f}")
    if audit_sha:
        print(f"AUDIT_SHA256={audit_sha}")


def main() -> int:
    before = shutil.disk_usage("/").free
    events: list[str] = [f"free_before={before}"]
    try:
        open_prs = fetch_open_prs() | fetch_open_prs() | ALWAYS_PRESERVE
    except Exception:
        finish("BLOCKED_GITHUB", 0, 0, 0, before, before)
        return 0

    if not ROOT.exists() or ROOT.is_symlink() or not ROOT.is_dir():
        finish("BLOCKED_ROOT", 0, 0, 0, before, before)
        return 0
    root_real = ROOT.resolve()

    wt = run("git", "-C", str(REPO), "worktree", "list", "--porcelain")
    if wt.returncode != 0:
        finish("BLOCKED_REGISTRY", 0, 0, 0, before, before)
        return 0

    candidates: list[tuple[int, Path]] = []
    preserved = 0
    skipped = 0
    for line in wt.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line[9:])
        match = re.fullmatch(r"pr-(\d+)", path.name)
        if not match or path.parent != ROOT:
            continue
        pr = int(match.group(1))
        if pr in open_prs:
            preserved += 1
            events.append(f"preserve_open_pr={pr}")
            continue
        candidates.append((pr, path))

    candidates.sort(key=lambda item: item[0])
    removed: list[int] = []
    for pr, path in candidates:
        if len(removed) >= MAX_REMOVE or shutil.disk_usage("/").free >= TARGET_FREE:
            break
        try:
            if path.is_symlink() or not path.exists() or not path.is_dir() or path.resolve().parent != root_real:
                skipped += 1
                events.append(f"skip_pr={pr}:unsafe_path")
                continue
        except OSError:
            skipped += 1
            events.append(f"skip_pr={pr}:path_error")
            continue

        status = run("git", "-C", str(path), "status", "--porcelain=v1", "--untracked-files=all")
        if status.returncode != 0:
            skipped += 1
            events.append(f"skip_pr={pr}:status_error")
            continue
        if status.stdout.strip():
            skipped += 1
            events.append(f"skip_pr={pr}:dirty")
            continue

        removal = run("git", "-C", str(REPO), "worktree", "remove", str(path))
        if removal.returncode != 0:
            skipped += 1
            events.append(f"skip_pr={pr}:remove_failed")
            continue
        removed.append(pr)
        events.append(f"removed_pr={pr}")

    run("git", "-C", str(REPO), "worktree", "prune", "--expire", "now")
    after = shutil.disk_usage("/").free

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.chmod(0o700)
    audit = AUDIT_DIR / ("validation-worktree-cleanup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".log")
    events.extend([
        f"removed_count={len(removed)}",
        f"preserved_open={preserved}",
        f"skipped={skipped}",
        f"free_after={after}",
    ])
    audit.write_text("\n".join(events) + "\n", encoding="utf-8")
    audit.chmod(0o600)
    digest = hashlib.sha256(audit.read_bytes()).hexdigest()
    finish("OK", len(removed), preserved, skipped, before, after, digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
