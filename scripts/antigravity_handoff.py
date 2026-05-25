from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


DEFAULT_FORBIDDEN_ACTIONS = (
    "No deploy.",
    "No secrets or credential handling.",
    "No runtime service changes, service restarts, timers, daemons, or process management.",
    "No merge, direct push to protected branches, or force push.",
    "No target repository execution beyond local validation commands explicitly listed in this pack.",
    "No private data handling.",
    "No Antigravity API integration.",
)
DEFAULT_PR_EXPECTATIONS = (
    "Open a pull request only; do not merge it.",
    "Keep the PR scoped to the allowed files and the source issue.",
    "Include a concise summary, changed files, validation results, and any unresolved risk.",
    "Leave merge authority outside Antigravity.",
)
DEFAULT_SAFETY_GATES = (
    "Stop if the requested work requires files outside the allowed list.",
    "Stop if secrets, private data, deploys, service changes, direct merges, or force pushes are requested.",
    "Stop if validation cannot be run or fails; report the failure in the PR.",
    "Final merge requires ChatGPT PR review plus Telegram-approved Runner merge.",
)


@dataclass(frozen=True)
class AntigravityHandoff:
    repository: str
    base_branch: str
    source_issue: str
    allowed_files: tuple[str, ...]
    validation_commands: tuple[str, ...]
    forbidden_files: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = DEFAULT_FORBIDDEN_ACTIONS
    pr_expectations: tuple[str, ...] = DEFAULT_PR_EXPECTATIONS
    safety_gates: tuple[str, ...] = DEFAULT_SAFETY_GATES


def _clean_required(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def _clean_items(items: tuple[str, ...] | list[str], name: str) -> tuple[str, ...]:
    cleaned = tuple(item.strip() for item in items if item.strip())
    if not cleaned:
        raise ValueError(f"{name} must include at least one value")
    return cleaned


def _optional_items(items: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(item.strip() for item in items if item.strip())


def _bullet_lines(items: tuple[str, ...]) -> list[str]:
    if not items:
        return ["- None."]
    return [f"- {item}" for item in items]


def normalize_issue(issue: str) -> str:
    cleaned = _clean_required(issue, "source_issue")
    return cleaned if cleaned.startswith("#") else f"#{cleaned}"


def build_handoff_pack(handoff: AntigravityHandoff) -> str:
    repository = _clean_required(handoff.repository, "repository")
    base_branch = _clean_required(handoff.base_branch, "base_branch")
    source_issue = normalize_issue(handoff.source_issue)
    allowed_files = _clean_items(handoff.allowed_files, "allowed_files")
    validation_commands = _clean_items(handoff.validation_commands, "validation_commands")
    forbidden_files = _optional_items(handoff.forbidden_files)
    forbidden_actions = _clean_items(handoff.forbidden_actions, "forbidden_actions")
    pr_expectations = _clean_items(handoff.pr_expectations, "pr_expectations")
    safety_gates = _clean_items(handoff.safety_gates, "safety_gates")

    lines: list[str] = [
        "Antigravity External Executor Handoff Pack",
        "",
        "Use this pack as the complete task prompt for Antigravity. It is intentionally plain text and contains no secrets.",
        "",
        "Context",
        f"- Repository: {repository}",
        f"- Base branch: {base_branch}",
        f"- Source issue: {source_issue}",
        "- Execution mode: external pull request only",
        "",
        "Task",
        f"- Implement the reviewed work described by source issue {source_issue}.",
        "- Keep the change small, reviewable, and limited to the allowed files below.",
        "",
        "Allowed files",
    ]
    lines.extend(_bullet_lines(allowed_files))
    lines.extend(["", "Forbidden files"])
    lines.extend(_bullet_lines(forbidden_files))
    lines.extend(["", "Forbidden actions"])
    lines.extend(_bullet_lines(forbidden_actions))
    lines.extend(["", "Validation commands"])
    lines.extend(f"- `{command}`" for command in validation_commands)
    lines.extend(["", "Pull request expectations"])
    lines.extend(_bullet_lines(pr_expectations))
    lines.extend(["", "Safety gates"])
    lines.extend(_bullet_lines(safety_gates))
    lines.extend(
        [
            "",
            "Required final output from Antigravity",
            "- Pull request URL.",
            "- Changed files.",
            "- Validation commands run and their results.",
            "- Explicit confirmation that no deploy, secrets, service changes, merge, or force push occurred.",
            "",
            "Merge boundary",
            "- Antigravity must not merge. Merge remains outside Antigravity and requires ChatGPT PR review plus Telegram-approved Runner merge.",
            "",
        ]
    )
    return "\n".join(lines)


def write_handoff_pack(handoff: AntigravityHandoff, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_handoff_pack(handoff), encoding="utf-8")
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a plain text Antigravity external executor handoff pack."
    )
    parser.add_argument("--repository", required=True, help="Target repository, for example alanua/Skeleton.")
    parser.add_argument("--base-branch", required=True, help="Base branch for the external PR.")
    parser.add_argument("--source-issue", required=True, help="Reviewed source issue number or reference.")
    parser.add_argument(
        "--allowed-file",
        action="append",
        default=[],
        help="File Antigravity may edit. Repeat for multiple files.",
    )
    parser.add_argument(
        "--forbidden-file",
        action="append",
        default=[],
        help="File Antigravity must not edit. Repeat for multiple files.",
    )
    parser.add_argument(
        "--validation-command",
        action="append",
        default=[],
        help="Validation command Antigravity should run. Repeat for multiple commands.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output path. Defaults to stdout.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    handoff = AntigravityHandoff(
        repository=args.repository,
        base_branch=args.base_branch,
        source_issue=args.source_issue,
        allowed_files=tuple(args.allowed_file),
        forbidden_files=tuple(args.forbidden_file),
        validation_commands=tuple(args.validation_command),
    )
    pack = build_handoff_pack(handoff)
    if args.output:
        write_handoff_pack(handoff, args.output)
        print(f"wrote {args.output}")
    else:
        print(pack)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
