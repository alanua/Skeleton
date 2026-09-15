from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runner_backlog_audit import audit_runner_backlog, compact_report


DEFAULT_REPO = "alanua/Skeleton"
ISSUE_JSON_FIELDS = "number,title,body,state,url,closed,labels"


class RunnerBacklogAuditError(RuntimeError):
    pass


def run_command(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def load_open_issues(repo: str, limit: int) -> list[dict[str, Any]]:
    code, output = run_command(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            "is:issue",
            "--limit",
            str(limit),
            "--json",
            ISSUE_JSON_FIELDS,
        ]
    )
    if code != 0:
        raise RunnerBacklogAuditError(f"gh issue list failed:\n{output}")
    parsed = json.loads(output or "[]")
    if not isinstance(parsed, list):
        raise RunnerBacklogAuditError("gh issue list returned non-list JSON.")
    return [issue for issue in parsed if isinstance(issue, dict)]


def load_branch_head(repo: str, branch: str) -> str | None:
    code, output = run_command(
        [
            "gh",
            "api",
            f"repos/{repo}/git/ref/heads/{branch}",
            "--jq",
            ".object.sha",
        ]
    )
    if code != 0:
        return None
    value = output.strip().splitlines()[-1] if output.strip() else ""
    return value.lower() if value else None


def load_pr_heads(repo: str, pr_numbers: set[int]) -> dict[int, str]:
    heads: dict[int, str] = {}
    for number in sorted(pr_numbers):
        code, output = run_command(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--repo",
                repo,
                "--json",
                "headRefOid",
                "--jq",
                ".headRefOid",
            ]
        )
        if code != 0:
            continue
        value = output.strip().splitlines()[-1] if output.strip() else ""
        if value:
            heads[number] = value.lower()
    return heads


def _expected_branches_and_prs(issues: list[dict[str, Any]]) -> tuple[set[str], set[int]]:
    provisional = audit_runner_backlog(issues)
    branches: set[str] = set()
    prs: set[int] = set()
    for category_items in provisional["items"].values():
        for item in category_items:
            if item.get("expected_base_sha"):
                branches.add(str(item.get("base_branch") or "main"))
            if item.get("pull_request") and item.get("expected_head_sha"):
                prs.add(int(item["pull_request"]))
    return branches, prs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of open public Runner backlog issues. The command "
            "classifies issues but never closes, relabels, or edits them."
        )
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--format",
        choices=("compact", "json"),
        default="compact",
        help="Output compact Dashboard text or deterministic JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit < 1:
        print("ERROR: --limit must be positive.", file=sys.stderr)
        return 1

    try:
        issues = load_open_issues(args.repo, args.limit)
        branches, pr_numbers = _expected_branches_and_prs(issues)
        base_heads = {
            branch: head
            for branch in sorted(branches)
            if (head := load_branch_head(args.repo, branch)) is not None
        }
        audit = audit_runner_backlog(
            issues,
            base_heads=base_heads,
            pr_heads=load_pr_heads(args.repo, pr_numbers),
            repo=args.repo,
        )
    except (json.JSONDecodeError, OSError, RunnerBacklogAuditError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(audit, sort_keys=True, separators=(",", ":")))
    else:
        print(compact_report(audit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
