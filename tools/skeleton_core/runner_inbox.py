from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ALLOWED_PACKET_TYPES = frozenset({"append_review_queue_entries"})
ALLOWED_TARGETS = frozenset({"projects/skeleton/REVIEW_QUEUE.yaml"})
REQUIRED_ENTRY_FIELDS = frozenset(
    {
        "id",
        "source_batch",
        "date",
        "classification",
        "target_project",
        "summary",
        "existing_match",
        "risk",
        "recommended_action",
        "status",
        "canon_status",
    }
)
ALLOWED_STATUS = frozenset({"REVIEW", "BACKLOG", "REJECTED"})
ALLOWED_CANON_STATUS = frozenset({"not_canon_until_promoted", "rejected_not_canon"})
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


class RunnerInboxError(ValueError):
    """Raised when a Runner inbox packet is outside the bounded contract."""


@dataclass(frozen=True)
class RunnerInboxReport:
    status: str
    packet_type: str
    target: str
    appended_entries: int
    blocked_reason: str | None = None

    def compact(self) -> str:
        if self.status == "blocked":
            return f"blocked {self.packet_type} -> {self.target}: {self.blocked_reason}"
        return f"appended {self.appended_entries} entries to {self.target}"


def process_runner_inbox(packet_path: str | Path, repo_root: str | Path | None = None) -> RunnerInboxReport:
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    packet_file = Path(packet_path)
    try:
        packet = _load_packet(packet_file)
    except RunnerInboxError as exc:
        return RunnerInboxReport("blocked", "<unreadable>", "<unknown>", 0, str(exc))

    try:
        packet_type = _require_string(packet, "type")
        target = _require_string(packet, "target")
        if packet_type not in ALLOWED_PACKET_TYPES:
            raise RunnerInboxError(f"packet type is not allowlisted: {packet_type}")
        if target not in ALLOWED_TARGETS:
            raise RunnerInboxError(f"target path is not allowlisted: {target}")
        entries = _validate_entries(packet.get("entries"))
        target_path = _resolve_allowed_target(root, target)
        _append_entries(target_path, entries)
    except RunnerInboxError as exc:
        packet_type = str(packet.get("type", "<missing>"))
        target = str(packet.get("target", "<missing>"))
        return RunnerInboxReport("blocked", packet_type, target, 0, str(exc))

    return RunnerInboxReport("appended", packet_type, target, len(entries), None)


def _load_packet(packet_path: Path) -> dict[str, Any]:
    with packet_path.open("r", encoding="utf-8") as handle:
        packet = yaml.safe_load(handle)
    if not isinstance(packet, dict):
        raise RunnerInboxError("packet must be a mapping")
    _check_no_secret_like_content(packet)
    return packet


def _require_string(packet: dict[str, Any], key: str) -> str:
    value = packet.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RunnerInboxError(f"{key} must be a non-empty string")
    return value


def _validate_entries(raw_entries: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list) or not raw_entries:
        raise RunnerInboxError("entries must be a non-empty list")

    entries: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise RunnerInboxError(f"entry {index} must be a mapping")
        keys = set(entry)
        missing = REQUIRED_ENTRY_FIELDS - keys
        extra = keys - REQUIRED_ENTRY_FIELDS
        if missing:
            raise RunnerInboxError(f"entry {index} missing fields: {', '.join(sorted(missing))}")
        if extra:
            raise RunnerInboxError(f"entry {index} has non-allowlisted fields: {', '.join(sorted(extra))}")

        entry_id = entry["id"]
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise RunnerInboxError(f"entry {index} id must be a non-empty string")
        if entry_id in ids:
            raise RunnerInboxError(f"duplicate packet entry id: {entry_id}")
        ids.add(entry_id)

        for field in REQUIRED_ENTRY_FIELDS:
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise RunnerInboxError(f"entry {index} {field} must be a non-empty string")

        classification_parts = set(entry["classification"].split("/"))
        if not classification_parts <= ALLOWED_STATUS:
            raise RunnerInboxError(
                f"entry {index} classification must use REVIEW/BACKLOG/REJECTED only"
            )
        if entry["status"] not in ALLOWED_STATUS:
            raise RunnerInboxError(f"entry {index} status is not allowlisted: {entry['status']}")
        if entry["canon_status"] not in ALLOWED_CANON_STATUS:
            raise RunnerInboxError(
                f"entry {index} canon_status is not allowlisted: {entry['canon_status']}"
            )
        if entry["status"] == "REJECTED" or entry["classification"] == "REJECTED":
            if entry["canon_status"] != "rejected_not_canon":
                raise RunnerInboxError(f"entry {index} rejected items must remain rejected_not_canon")
        elif entry["canon_status"] != "not_canon_until_promoted":
            raise RunnerInboxError(f"entry {index} cannot promote canon from packet")

        _check_no_secret_like_content(entry)
        entries.append(dict(entry))

    return entries


def _resolve_allowed_target(root: Path, target: str) -> Path:
    root = root.resolve()
    target_path = (root / target).resolve()
    if target_path != (root / "projects/skeleton/REVIEW_QUEUE.yaml").resolve():
        raise RunnerInboxError(f"target path is not allowlisted: {target}")
    if not target_path.is_file():
        raise RunnerInboxError(f"target file does not exist: {target}")
    return target_path


def _append_entries(target_path: Path, entries: list[dict[str, Any]]) -> None:
    original = target_path.read_text(encoding="utf-8")
    queue = yaml.safe_load(original)
    if not isinstance(queue, dict) or not isinstance(queue.get("entries"), list):
        raise RunnerInboxError("target YAML must contain an entries list")

    existing_ids = {entry.get("id") for entry in queue["entries"] if isinstance(entry, dict)}
    duplicates = sorted(entry["id"] for entry in entries if entry["id"] in existing_ids)
    if duplicates:
        raise RunnerInboxError(f"entry ids already exist in target: {', '.join(duplicates)}")

    rendered = _render_entries(entries)
    candidate = _with_trailing_newline(original) + rendered
    parsed_candidate = yaml.safe_load(candidate)
    if not isinstance(parsed_candidate, dict) or not isinstance(parsed_candidate.get("entries"), list):
        raise RunnerInboxError("append would produce invalid review queue YAML")
    if len(parsed_candidate["entries"]) != len(queue["entries"]) + len(entries):
        raise RunnerInboxError("append would not preserve review queue ordering")

    append_text = candidate[len(original) :]
    with target_path.open("a", encoding="utf-8") as handle:
        handle.write(append_text)
    parsed_after_write = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    if parsed_after_write != parsed_candidate:
        raise RunnerInboxError("target YAML validation failed after write")


def _render_entries(entries: list[dict[str, Any]]) -> str:
    rendered_entries: list[str] = []
    for entry in entries:
        rendered = yaml.safe_dump(
            [entry],
            sort_keys=False,
            allow_unicode=False,
            default_flow_style=False,
            width=120,
        )
        rendered_entries.append(_indent_entry(rendered.rstrip()))
    return "\n".join(rendered_entries) + "\n"


def _indent_entry(rendered: str) -> str:
    lines = rendered.splitlines()
    indented: list[str] = []
    for line in lines:
        if line.startswith("- "):
            indented.append("  " + line)
        elif line.startswith("  "):
            indented.append("  " + line)
        else:
            indented.append("    " + line)
    return "\n".join(indented)


def _with_trailing_newline(text: str) -> str:
    if text.endswith("\n\n"):
        return text
    if text.endswith("\n"):
        return text + "\n"
    return text + "\n\n"


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
        raise RunnerInboxError("secret-like or private content is blocked")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise RunnerInboxError("secret-like or private content is blocked")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process a bounded Skeleton Runner inbox packet.")
    parser.add_argument("packet", help="Path to a local YAML Runner inbox packet.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    args = parser.parse_args(argv)

    try:
        report = process_runner_inbox(args.packet, args.repo_root)
    except RunnerInboxError as exc:
        report = RunnerInboxReport("blocked", "<unreadable>", "<unknown>", 0, str(exc))

    print(json.dumps(report.__dict__, sort_keys=True))
    print(report.compact())
    return 0 if report.status == "appended" else 2


if __name__ == "__main__":
    raise SystemExit(main())
