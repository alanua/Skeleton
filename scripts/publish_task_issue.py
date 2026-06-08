from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_REPO = "alanua/Skeleton"
READY_LABEL = "runner:ready"
OPENING_FENCE = "```task"
CLOSING_FENCE = "```"
REQUIRED_SECTIONS = (
    "classification",
    "repo",
    "intent",
    "goal",
    "scope",
    "non_goals",
    "acceptance",
)

_SECTION_RE_TEMPLATE = r"(?m)^{section}\s*:"


@dataclass(frozen=True)
class PublishedIssue:
    number: int
    url: str


class PublishError(RuntimeError):
    pass


def run_command(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout + result.stderr


def read_body_file(path: Path) -> str:
    if path.suffix.lower() != ".md":
        raise PublishError("Task issue body source must be a local .md file.")
    if not path.is_file():
        raise PublishError(f"Task issue body file does not exist: {path}")

    body = path.read_text(encoding="utf-8")
    if body.strip() == "":
        raise PublishError("Task issue body file is empty.")
    return body


def validate_task_body(body: str) -> None:
    lines = body.splitlines()
    if not lines or lines[0] != OPENING_FENCE:
        raise PublishError("Task issue body must start with an opening ```task fence.")

    stripped_newlines = body.rstrip("\n")
    if not stripped_newlines.endswith(CLOSING_FENCE):
        raise PublishError("Task issue body must end with a closing ``` fence at EOF.")
    if stripped_newlines.splitlines()[-1] != CLOSING_FENCE:
        raise PublishError("Task issue body closing fence must be the final line.")

    fenced_body = "\n".join(stripped_newlines.splitlines()[1:-1])
    for section in REQUIRED_SECTIONS:
        pattern = _SECTION_RE_TEMPLATE.format(section=re.escape(section))
        if re.search(pattern, fenced_body) is None:
            raise PublishError(f"Task issue body is missing required section: {section}")


def load_and_validate_body(path: Path) -> str:
    body = read_body_file(path)
    validate_task_body(body)
    return body


def _parse_issue_json(output: str) -> dict[str, Any]:
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise PublishError("gh issue view returned non-object JSON.")
    return parsed


def _view_issue(repo: str, issue_ref: str | int) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue_ref),
            "--repo",
            repo,
            "--json",
            "number,url,body",
        ]
    )
    if code != 0:
        raise PublishError(f"gh issue view failed:\n{output}")
    return _parse_issue_json(output)


def _create_issue(repo: str, title: str, body_file: Path) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ]
    )
    if code != 0:
        raise PublishError(f"gh issue create failed:\n{output}")

    issue_ref = output.strip().splitlines()[-1]
    if issue_ref == "":
        raise PublishError("gh issue create did not return an issue reference.")
    return _view_issue(repo, issue_ref)


def _update_issue(repo: str, issue_number: int, body_file: Path) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repo,
            "--body-file",
            str(body_file),
        ]
    )
    if code != 0:
        raise PublishError(f"gh issue edit failed:\n{output}")
    return _view_issue(repo, issue_number)


def _add_ready_label(repo: str, issue_number: int) -> None:
    code, output = run_command(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            repo,
            "--add-label",
            READY_LABEL,
        ]
    )
    if code != 0:
        raise PublishError(f"gh issue ready-label edit failed:\n{output}")


def _verified_issue(issue: dict[str, Any], expected_body: str) -> PublishedIssue:
    remote_body = issue.get("body")
    if remote_body != expected_body:
        raise PublishError("Remote issue body does not match local file body after publish.")

    number = issue.get("number")
    url = issue.get("url")
    if not isinstance(number, int) or number < 1:
        raise PublishError("Remote issue read-back did not include a valid issue number.")
    if not isinstance(url, str) or url == "":
        raise PublishError("Remote issue read-back did not include a valid issue URL.")
    return PublishedIssue(number=number, url=url)


def publish_task_issue(
    *,
    repo: str,
    body_file: Path,
    title: str | None = None,
    issue_number: int | None = None,
) -> PublishedIssue:
    body = load_and_validate_body(body_file)

    if issue_number is None:
        if title is None or title.strip() == "":
            raise PublishError("--title is required when creating a new issue.")
        issue = _create_issue(repo, title, body_file)
    else:
        issue = _update_issue(repo, issue_number, body_file)

    published = _verified_issue(issue, body)
    _add_ready_label(repo, published.number)
    return published


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or update a runnable Runner task issue from a validated "
            "local markdown body file, verify read-back, then add runner:ready."
        )
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--title", help="Required when creating a new issue.")
    parser.add_argument("--issue", type=int, help="Existing issue number to update.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        issue = publish_task_issue(
            repo=args.repo,
            body_file=args.body_file,
            title=args.title,
            issue_number=args.issue,
        )
    except (OSError, json.JSONDecodeError, PublishError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"number": issue.number, "url": issue.url}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
