from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runner_task import PRIVACY_BOUNDARIES


TASK_FENCE_OPEN_RE = re.compile(r"^\s*```task\s*$")
FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")
FIELD_LINE_RE = re.compile(r"^\s*[A-Za-z][A-Za-z0-9 _-]{0,80}:\s*.*$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/@+-]{0,511}$")

PRIVACY_BOUNDARY_ALIASES = frozenset(
    {
        "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY",
        "PUBLIC_SAFE_QUEUE_AND_PR_METADATA_ONLY",
        "PUBLIC_SAFE_QUEUE_METADATA_ONLY",
        "PRIVATE_LOCAL_ONLY",
    }
)
KNOWN_PRIVACY_BOUNDARIES = PRIVACY_BOUNDARIES | PRIVACY_BOUNDARY_ALIASES

NEGATIVE_PREFIXES = (
    "no ",
    "do not ",
    "don't ",
    "never ",
    "must not ",
    "forbid ",
    "forbidden ",
    "without ",
)
AFFIRMATIVE_PREFIXES = (
    "allow ",
    "allowed ",
    "permit ",
    "permitted ",
    "may ",
    "can ",
    "must ",
    "required ",
    "require ",
    "run ",
    "create ",
    "modify ",
)
ACTION_ALIASES = {
    "git add": ("git add", "stage", "staging"),
    "git commit": ("git commit", "commit"),
    "git push": ("git push", "push"),
    "gh pr create": ("gh pr create", "create pr", "open pr", "pull request"),
    "gh": ("gh ", "github cli"),
    "secrets": ("secret", "token", "credential", "private data"),
    "protected paths": ("protected path", "protected file", "poller", "gate"),
}


@dataclass(frozen=True)
class LintFinding:
    code: str
    message: str

    def to_mapping(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class _TaskBlock:
    content: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class _Occurrence:
    source: str
    value: object


def lint_issue_body(body: str) -> tuple[LintFinding, ...]:
    findings: list[LintFinding] = []
    task_blocks, fence_findings = _extract_task_blocks(body)
    findings.extend(fence_findings)

    metadata = _metadata_before_first_task(body)
    parsed_task = _parse_task_block(task_blocks[0], findings) if task_blocks else {}

    allowed_files = _collect_values(
        metadata=metadata,
        task_fields=parsed_task,
        public_field="Allowed Files",
        typed_key="allowed_files",
    )
    findings.extend(_lint_allowed_files(allowed_files))

    idempotency_keys = _collect_values(
        metadata=metadata,
        task_fields=parsed_task,
        public_field="Idempotency Key",
        typed_key="idempotency_key",
    )
    findings.extend(_lint_idempotency_keys(idempotency_keys))

    privacy_boundaries = _collect_values(
        metadata=metadata,
        task_fields=parsed_task,
        public_field="Privacy Boundary",
        typed_key="privacy_boundary",
    )
    findings.extend(_lint_privacy_boundaries(privacy_boundaries))

    forbidden_actions = _collect_values(
        metadata=metadata,
        task_fields=parsed_task,
        public_field="Forbidden Actions",
        typed_key="forbidden_actions",
    )
    findings.extend(_lint_forbidden_actions(forbidden_actions))

    return tuple(_dedupe_findings(findings))


def lint_issue_body_file(path: Path) -> tuple[LintFinding, ...]:
    return lint_issue_body(path.read_text(encoding="utf-8"))


def _extract_task_blocks(body: str) -> tuple[list[_TaskBlock], list[LintFinding]]:
    lines = (body or "").splitlines()
    blocks: list[_TaskBlock] = []
    findings: list[LintFinding] = []
    index = 0
    while index < len(lines):
        if TASK_FENCE_OPEN_RE.match(lines[index]) is None:
            index += 1
            continue
        start = index + 1
        content: list[str] = []
        index += 1
        while index < len(lines) and FENCE_CLOSE_RE.match(lines[index]) is None:
            content.append(lines[index])
            index += 1
        if index >= len(lines):
            findings.append(
                LintFinding(
                    "MALFORMED_TASK_BLOCK",
                    f"task fence opened on line {start} without a closing fence",
                )
            )
            break
        blocks.append(_TaskBlock("\n".join(content), start, index + 1))
        index += 1
    if not blocks and not any(f.code == "MALFORMED_TASK_BLOCK" for f in findings):
        findings.append(
            LintFinding(
                "MISSING_TASK_BLOCK",
                "issue body must include a fenced ```task block",
            )
        )
    if len(blocks) > 1:
        findings.append(
            LintFinding(
                "MULTIPLE_TASK_BLOCKS",
                "issue body must contain exactly one fenced ```task block",
            )
        )
    return blocks, findings


def _metadata_before_first_task(body: str) -> str:
    match = re.search(r"(?m)^\s*```task\s*$", body or "")
    return (body or "")[: match.start()] if match else (body or "")


def _parse_task_block(
    task_block: _TaskBlock,
    findings: list[LintFinding],
) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(task_block.content)
    except yaml.YAMLError:
        findings.append(
            LintFinding(
                "MALFORMED_TASK_BLOCK",
                f"task block YAML is malformed near line {task_block.start_line}",
            )
        )
        return {}
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        findings.append(
            LintFinding("MALFORMED_TASK_BLOCK", "task block must be a YAML mapping")
        )
        return {}
    return parsed


def _collect_values(
    *,
    metadata: str,
    task_fields: dict[str, Any],
    public_field: str,
    typed_key: str,
) -> list[_Occurrence]:
    values: list[_Occurrence] = []
    if typed_key in task_fields:
        values.append(_Occurrence("task_block", task_fields[typed_key]))

    values.extend(
        _metadata_field_occurrences(metadata, public_field, typed_key)
    )
    values.extend(_metadata_yaml_occurrences(metadata, typed_key))
    return values


def _metadata_yaml_occurrences(metadata: str, key: str) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    lines = (metadata or "").splitlines()
    index = 0
    while index < len(lines):
        if re.fullmatch(r"\s*```\s*(?:yaml|yml)?\s*", lines[index]) is None:
            index += 1
            continue
        start_line = index + 1
        yaml_lines: list[str] = []
        index += 1
        while index < len(lines) and FENCE_CLOSE_RE.match(lines[index]) is None:
            yaml_lines.append(lines[index])
            index += 1
        if index < len(lines):
            index += 1
        try:
            parsed = yaml.safe_load("\n".join(yaml_lines)) if yaml_lines else None
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and key in parsed:
            occurrences.append(_Occurrence(f"metadata_yaml_line_{start_line}", parsed[key]))
    return occurrences


def _metadata_field_occurrences(
    metadata: str,
    public_field: str,
    typed_key: str,
) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    lines = (metadata or "").splitlines()
    field_names = (public_field, typed_key) if public_field != typed_key else (typed_key,)
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.fullmatch(r"\s*```\s*(?:[A-Za-z0-9_-]+)?\s*", line):
            index += 1
            while index < len(lines) and FENCE_CLOSE_RE.match(lines[index]) is None:
                index += 1
            index += 1
            continue
        for field_name in field_names:
            inline = re.fullmatch(
                rf"\s*{re.escape(field_name)}:\s*(?P<value>\S(?:.*\S)?)?\s*",
                line,
            )
            if inline is None:
                continue
            value = inline.group("value")
            if value is not None:
                occurrences.append(_Occurrence(f"metadata_line_{index + 1}", value))
                break
            items: list[str] = []
            malformed_items = False
            for item in lines[index + 1 :]:
                if not item.strip():
                    break
                if FIELD_LINE_RE.fullmatch(item) or re.fullmatch(
                    r"\s*```\s*(?:yaml|yml)?\s*", item
                ):
                    break
                match = re.fullmatch(r"\s*-\s+(?P<value>\S(?:.*\S)?)\s*", item)
                if match is None:
                    malformed_items = True
                    continue
                items.append(match.group("value"))
            occurrences.append(
                _Occurrence(
                    f"metadata_line_{index + 1}",
                    {"__malformed_multiline__": True} if malformed_items else items,
                )
            )
            break
        index += 1
    return occurrences


def _lint_allowed_files(occurrences: list[_Occurrence]) -> list[LintFinding]:
    if not occurrences:
        return [
            LintFinding(
                "MISSING_ALLOWED_FILES",
                "allowed_files must be explicitly declared before queueing",
            )
        ]
    findings: list[LintFinding] = []
    paths: list[str] = []
    for occurrence in occurrences:
        typed_source = not occurrence.source.startswith("metadata_line_")
        if typed_source and not isinstance(occurrence.value, (list, tuple)):
            findings.append(
                LintFinding(
                    "MALFORMED_ALLOWED_FILES",
                    f"allowed_files at {occurrence.source} must be a non-empty string list",
                )
            )
            continue
        values = _string_sequence(
            occurrence.value,
            csv=occurrence.source.startswith("metadata_line_"),
        )
        if values is None:
            findings.append(
                LintFinding(
                    "MALFORMED_ALLOWED_FILES",
                    f"allowed_files at {occurrence.source} must be a non-empty string list",
                )
            )
            continue
        paths.extend(values)
        for path in values:
            if any(char in path for char in "*?[]{}"):
                findings.append(
                    LintFinding(
                        "UNSAFE_WILDCARD_PATH",
                        f"allowed_files path uses wildcard syntax: {path}",
                    )
                )
            if not _is_safe_repository_path(path):
                findings.append(
                    LintFinding(
                        "MALFORMED_ALLOWED_FILES",
                        f"allowed_files path is not repository-relative and bounded: {path}",
                    )
                )
    if len(set(paths)) != len(paths):
        findings.append(
            LintFinding("MALFORMED_ALLOWED_FILES", "allowed_files contains duplicates")
        )
    return findings


def _lint_idempotency_keys(occurrences: list[_Occurrence]) -> list[LintFinding]:
    if not occurrences:
        return [
            LintFinding(
                "MISSING_IDEMPOTENCY_KEY",
                "idempotency_key must be explicitly declared before queueing",
            )
        ]
    findings: list[LintFinding] = []
    keys: list[str] = []
    for occurrence in occurrences:
        if not isinstance(occurrence.value, str) or not occurrence.value.strip():
            findings.append(
                LintFinding(
                    "MALFORMED_IDEMPOTENCY_KEY",
                    f"idempotency_key at {occurrence.source} must be a non-empty string",
                )
            )
            continue
        key = occurrence.value.strip()
        keys.append(key)
        if TOKEN_RE.fullmatch(key) is None:
            findings.append(
                LintFinding(
                    "MALFORMED_IDEMPOTENCY_KEY",
                    "idempotency_key must be a bounded token",
                )
            )
    if len(set(keys)) > 1:
        findings.append(
            LintFinding(
                "CONFLICTING_IDEMPOTENCY_KEYS",
                "idempotency_key has conflicting values across metadata and task block",
            )
        )
    elif len(keys) > 1:
        findings.append(
            LintFinding(
                "DUPLICATE_IDEMPOTENCY_KEY",
                "idempotency_key is declared more than once",
            )
        )
    return findings


def _lint_privacy_boundaries(occurrences: list[_Occurrence]) -> list[LintFinding]:
    if not occurrences:
        return [
            LintFinding(
                "MISSING_PRIVACY_BOUNDARY",
                "privacy_boundary must be explicitly declared before queueing",
            )
        ]
    findings: list[LintFinding] = []
    boundaries: list[str] = []
    for occurrence in occurrences:
        if not isinstance(occurrence.value, str) or not occurrence.value.strip():
            findings.append(
                LintFinding(
                    "MISSING_PRIVACY_BOUNDARY",
                    f"privacy_boundary at {occurrence.source} is blank",
                )
            )
            continue
        boundary = occurrence.value.strip()
        boundaries.append(boundary)
        if boundary not in KNOWN_PRIVACY_BOUNDARIES:
            findings.append(
                LintFinding(
                    "INVALID_PRIVACY_BOUNDARY",
                    f"privacy_boundary is not recognized: {boundary}",
                )
            )
    if len(set(boundaries)) > 1:
        findings.append(
            LintFinding(
                "CONFLICTING_PRIVACY_BOUNDARIES",
                "privacy_boundary has conflicting values across metadata and task block",
            )
        )
    return findings


def _lint_forbidden_actions(occurrences: list[_Occurrence]) -> list[LintFinding]:
    if not occurrences:
        return []
    findings: list[LintFinding] = []
    actions: list[str] = []
    for occurrence in occurrences:
        values = _string_sequence(
            occurrence.value,
            csv=occurrence.source.startswith("metadata_line_"),
        )
        if values is None:
            findings.append(
                LintFinding(
                    "MALFORMED_FORBIDDEN_ACTIONS",
                    f"forbidden_actions at {occurrence.source} must be a string list",
                )
            )
            continue
        actions.extend(values)

    negative: set[str] = set()
    affirmative: set[str] = set()
    for action in actions:
        normalized = _normalize_action(action)
        if _has_prefix(normalized, NEGATIVE_PREFIXES):
            negative.update(_mentioned_action_aliases(normalized))
        if _has_prefix(normalized, AFFIRMATIVE_PREFIXES):
            affirmative.update(_mentioned_action_aliases(normalized))
    conflicts = sorted(negative & affirmative)
    if conflicts:
        findings.append(
            LintFinding(
                "CONTRADICTORY_FORBIDDEN_ACTIONS",
                "forbidden_actions both forbids and permits: " + ", ".join(conflicts),
            )
        )
    return findings


def _string_sequence(value: object, *, csv: bool) -> tuple[str, ...] | None:
    if isinstance(value, dict) and value.get("__malformed_multiline__") is True:
        return None
    if isinstance(value, str):
        values = value.split(",") if csv else [value]
        result = tuple(item.strip() for item in values if item.strip())
        return result or None
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        result = tuple(item.strip() for item in value if item.strip())
        return result or None
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        result = tuple(item.strip() for item in value if item.strip())
        return result or None
    return None


def _is_safe_repository_path(path: str) -> bool:
    relative_path = Path(path)
    return (
        path == path.strip()
        and path != ""
        and SAFE_PATH_RE.fullmatch(path) is not None
        and not relative_path.is_absolute()
        and "\\" not in path
        and "//" not in path
        and not path.endswith("/")
        and not any(segment in {"", ".", ".."} for segment in path.split("/"))
    )


def _normalize_action(action: str) -> str:
    return re.sub(r"\s+", " ", action.strip().lower())


def _has_prefix(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def _mentioned_action_aliases(action: str) -> set[str]:
    matches: set[str] = set()
    for canonical, aliases in ACTION_ALIASES.items():
        if any(alias in action for alias in aliases):
            matches.add(canonical)
    if not matches:
        matches.add(re.sub(r"^(?:" + "|".join(re.escape(p) for p in NEGATIVE_PREFIXES + AFFIRMATIVE_PREFIXES) + r")", "", action).strip())
    return matches


def _dedupe_findings(findings: list[LintFinding]) -> list[LintFinding]:
    seen: set[tuple[str, str]] = set()
    deduped: list[LintFinding] = []
    for finding in findings:
        key = (finding.code, finding.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministically lint a Skeleton Runner task issue body."
    )
    parser.add_argument("body_file", type=Path)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable findings.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    findings = lint_issue_body_file(args.body_file)
    if args.json:
        print(json.dumps([finding.to_mapping() for finding in findings], indent=2))
    elif findings:
        for finding in findings:
            print(f"{finding.code}: {finding.message}", file=sys.stderr)
    else:
        print("OK")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
