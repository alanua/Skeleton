from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Callable, Mapping

import yaml


TASK_ID = "private_memory_phase_a_inventory"
SCHEMA_VERSION = "skeleton.private_memory_source_inventory.v1"
REPORT_SCHEMA_VERSION = "skeleton.private_memory_source_inventory.private_report.v1"
FIXED_ALIASES = (
    "private_memory_root",
    "runner_memory_root",
    "hermes_runtime_root",
    "agent_dev_root",
    "hermes_workspace_root",
    "hermes_artifacts_root",
)
CANDIDATE_CATEGORIES = (
    "chat_export_candidate",
    "memory_database_candidate",
    "manifest_candidate",
    "project_handoff_candidate",
    "document_candidate",
    "other_candidate",
)
LIMITS = {
    "max_depth": (0, 8, 3),
    "max_entries_per_root": (1, 5000, 500),
    "max_total_entries": (1, 20000, 2500),
    "timeout_seconds": (1, 30, 5),
}
SECRET_NAME_RE = re.compile(
    r"(?i)(secret|token|credential|password|passwd|apikey|api_key|private[_-]?key|"
    r"\.pem$|\.key$|id_rsa|id_ed25519|\.env(?:\.|$))"
)
INFRA_NAME_RE = re.compile(
    r"(?i)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.git|node_modules|"
    r"\.venv|venv|dist|build|target|\.cache|\.tox|coverage|\.coverage)"
)
SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class InventoryResult:
    status: str
    success_criteria: str
    lines: list[str]
    private_report_path: Path | None = None


def execute_private_memory_phase_a_inventory(
    body: str = "",
    *,
    env: Mapping[str, str] | None = None,
    extract_task_block: Callable[[str], str | None] | None = None,
    now: Callable[[], float] | None = None,
) -> InventoryResult:
    environ = env if env is not None else os.environ
    clock = now if now is not None else time.monotonic
    parsed, reason = _parse_issue_options(body, extract_task_block=extract_task_block)
    if reason is not None:
        return _blocked(reason, selected_count=0)

    selected_aliases = tuple(parsed.get("aliases", FIXED_ALIASES))
    limits = dict(parsed.get("limits", _default_limits()))
    private_root_text = _resolve_alias_text("private_memory_root", environ)
    private_root = Path(private_root_text) if private_root_text else None
    private_root_reason = _validate_absolute_root(private_root)
    if private_root_reason is not None:
        return _blocked(private_root_reason, selected_count=len(selected_aliases))

    roots: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    counts = _empty_counts(selected_count=len(selected_aliases))
    start = clock()
    for alias in selected_aliases:
        root_text = _resolve_alias_text(alias, environ)
        root_record, root_candidates = _scan_alias_root(
            alias,
            root_text,
            limits=limits,
            total_counts=counts,
            start=start,
            clock=clock,
        )
        roots.append(root_record)
        candidates.extend(root_candidates)

    readable_roots = sum(1 for root in roots if root["state"] == "readable")
    counts["root_readable_count"] = readable_roots
    counts["root_unreadable_count"] = len(roots) - readable_roots
    if readable_roots == 0:
        return _blocked("no_readable_roots", selected_count=len(selected_aliases), counts=counts)

    degraded_reasons = _degraded_reasons(roots, counts)
    report_payload = {
        "schema": REPORT_SCHEMA_VERSION,
        "task_id": TASK_ID,
        "created_unix": int(time.time()),
        "limits": limits,
        "roots": roots,
        "candidates": candidates,
        "aggregate": counts,
        "content_files_read": False,
        "symlink_traversal": False,
        "runtime_private_action": False,
    }
    try:
        report_path, report_sha = _write_private_report(private_root, report_payload)
    except RuntimeError as exc:
        reason_text = str(exc)
        safe_reason = reason_text if SAFE_TOKEN_RE.fullmatch(reason_text) else "private_report_write_failed"
        return _blocked(safe_reason, selected_count=len(selected_aliases), counts=counts)
    except OSError:
        return _blocked("private_report_write_failed", selected_count=len(selected_aliases), counts=counts)
    lines = _public_lines(
        "DONE",
        "met",
        counts,
        report_sha=report_sha,
        reasons=degraded_reasons or ("inventory_complete",),
    )
    return InventoryResult(
        status="DONE",
        success_criteria="met",
        lines=lines,
        private_report_path=report_path,
    )


def _parse_issue_options(
    body: str,
    *,
    extract_task_block: Callable[[str], str | None] | None,
) -> tuple[dict[str, object], str | None]:
    block = extract_task_block(body) if extract_task_block is not None else _extract_task_block(body)
    if block is None or not block.strip():
        return {"aliases": FIXED_ALIASES, "limits": _default_limits()}, None
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}, "invalid_option_block"
    if loaded is None:
        return {"aliases": FIXED_ALIASES, "limits": _default_limits()}, None
    if not isinstance(loaded, dict):
        return {}, "invalid_option_block"

    allowed = {"aliases", "enabled_aliases", *LIMITS}
    unknown = set(loaded) - allowed
    if unknown:
        return {}, "unknown_option_key"
    if "aliases" in loaded and "enabled_aliases" in loaded:
        return {}, "duplicate_alias_options"

    aliases_value = loaded.get("aliases", loaded.get("enabled_aliases", FIXED_ALIASES))
    aliases, reason = _validate_aliases(aliases_value)
    if reason is not None:
        return {}, reason
    limits = _default_limits()
    for key, (minimum, maximum, _default) in LIMITS.items():
        if key not in loaded:
            continue
        value = loaded[key]
        if isinstance(value, bool) or not isinstance(value, int):
            return {}, "invalid_limit_value"
        if value < minimum or value > maximum:
            return {}, "limit_out_of_range"
        limits[key] = value
    return {"aliases": aliases, "limits": limits}, None


def _validate_aliases(value: object) -> tuple[tuple[str, ...], str | None]:
    if isinstance(value, str):
        raw_aliases: object = [value]
    else:
        raw_aliases = value
    if not isinstance(raw_aliases, list) or not raw_aliases:
        return (), "invalid_alias_list"
    aliases: list[str] = []
    for item in raw_aliases:
        if not isinstance(item, str) or not item or SAFE_TOKEN_RE.fullmatch(item) is None:
            return (), "invalid_alias_token"
        if any(marker in item for marker in ("/", "\\", ":", "~", "$", "`", "|", "&", ";")):
            return (), "invalid_alias_token"
        if "://" in item or item not in FIXED_ALIASES:
            return (), "unknown_alias"
        if item not in aliases:
            aliases.append(item)
    return tuple(aliases), None


def _default_limits() -> dict[str, int]:
    return {key: default for key, (_minimum, _maximum, default) in LIMITS.items()}


def _resolve_alias_text(alias: str, env: Mapping[str, str]) -> str | None:
    if alias == "private_memory_root":
        return env.get("SKELETON_PRIVATE_MEMORY_ROOT") or str(
            Path.home() / ".local" / "share" / "skeleton-private-memory"
        )
    if alias == "runner_memory_root":
        if env.get("SKELETON_RUNNER_MEMORY_DIR"):
            return env["SKELETON_RUNNER_MEMORY_DIR"]
        if env.get("SKELETON_RUNNER_MEMORY_DB"):
            return str(Path(env["SKELETON_RUNNER_MEMORY_DB"]).parent)
        if env.get("SKELETON_RUNNER_MEMORY_LEDGER"):
            return str(Path(env["SKELETON_RUNNER_MEMORY_LEDGER"]).parent)
        return None
    if alias == "hermes_runtime_root":
        return env.get("SKELETON_HERMES_RUNTIME_ROOT") or env.get("HERMES_RUNTIME_ROOT")
    if alias == "agent_dev_root":
        return env.get("RUNNER_APPROVED_WORKSPACE_ROOT") or "/home/agent/agent-dev"
    if alias == "hermes_workspace_root":
        return env.get("SKELETON_HERMES_WORKSPACE_ROOT") or env.get("HERMES_WORKSPACE_ROOT")
    if alias == "hermes_artifacts_root":
        return env.get("SKELETON_HERMES_ARTIFACTS_ROOT") or env.get("HERMES_ARTIFACTS_ROOT")
    return None


def _validate_absolute_root(path: Path | None) -> str | None:
    if path is None:
        return "private_root_unresolved"
    if not path.is_absolute():
        return "private_root_not_absolute"
    return None


def _scan_alias_root(
    alias: str,
    root_text: str | None,
    *,
    limits: Mapping[str, int],
    total_counts: dict[str, int],
    start: float,
    clock: Callable[[], float],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    record: dict[str, Any] = {
        "alias": alias,
        "state": "unresolved",
        "readable": False,
        "reason": "alias_unresolved",
        "entry_count": 0,
        "candidate_count": 0,
        "exclusions": {"secret_like": 0, "infrastructure_cache_name": 0, "symlink": 0},
        "truncated": False,
        "truncation_reasons": [],
    }
    if not root_text:
        return record, []
    root = Path(root_text)
    reason = _validate_scan_root(root)
    if reason is not None:
        record.update({"state": "unreadable", "reason": reason})
        return record, []

    try:
        iterator = os.scandir(root)
        iterator.close()
    except OSError:
        record.update({"state": "unreadable", "reason": "initial_scandir_failed"})
        return record, []

    record.update({"state": "readable", "readable": True, "reason": "readable"})
    candidates: list[dict[str, Any]] = []
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        current, depth = queue.pop(0)
        if _timed_out(start, clock, limits["timeout_seconds"]):
            _mark_truncated(record, total_counts, "timeout")
            break
        if depth > limits["max_depth"]:
            _mark_truncated(record, total_counts, "max_depth")
            continue
        try:
            with os.scandir(current) as entries:
                while True:
                    if _timed_out(start, clock, limits["timeout_seconds"]):
                        _mark_truncated(record, total_counts, "timeout")
                        return record, candidates
                    if total_counts["scanned_entry_count"] >= limits["max_total_entries"]:
                        _mark_truncated(record, total_counts, "max_total_entries")
                        return record, candidates
                    if record["entry_count"] >= limits["max_entries_per_root"]:
                        _mark_truncated(record, total_counts, "max_entries_per_root")
                        return record, candidates
                    try:
                        entry = next(entries)
                    except StopIteration:
                        break
                    total_counts["scanned_entry_count"] += 1
                    record["entry_count"] += 1
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        _mark_truncated(record, total_counts, "entry_stat_failed")
                        continue
                    name = entry.name
                    if stat.S_ISLNK(entry_stat.st_mode):
                        total_counts["symlink_count"] += 1
                        record["exclusions"]["symlink"] += 1
                        continue
                    if SECRET_NAME_RE.search(name):
                        total_counts["excluded_secret_like_count"] += 1
                        record["exclusions"]["secret_like"] += 1
                        continue
                    if INFRA_NAME_RE.fullmatch(name) or INFRA_NAME_RE.search(name):
                        total_counts["excluded_infrastructure_cache_name_count"] += 1
                        record["exclusions"]["infrastructure_cache_name"] += 1
                        continue
                    file_type = _file_type(entry_stat.st_mode)
                    category = _category_for_name(name, file_type)
                    if file_type == "directory" and depth < limits["max_depth"]:
                        queue.append((Path(entry.path), depth + 1))
                    if file_type == "regular_file":
                        candidate = _candidate_record(
                            alias, Path(entry.path), name, category, file_type, entry_stat
                        )
                        candidates.append(candidate)
                        record["candidate_count"] += 1
                        total_counts["candidate_count"] += 1
                        total_counts[f"{category}_count"] += 1
        except OSError:
            _mark_truncated(record, total_counts, "child_scandir_failed")
            continue
    return record, candidates


def _validate_scan_root(root: Path) -> str | None:
    if not root.is_absolute():
        return "alias_root_not_absolute"
    component_reason = _validate_existing_directory_components(
        root,
        missing_reason="alias_root_missing",
        symlink_reason="alias_root_symlink",
        not_directory_reason="alias_root_not_directory",
        lstat_failed_reason="alias_root_lstat_failed",
    )
    if component_reason is not None:
        return component_reason
    return None


def _validate_existing_directory_components(
    path: Path,
    *,
    missing_reason: str,
    symlink_reason: str,
    not_directory_reason: str,
    lstat_failed_reason: str,
) -> str | None:
    current = Path(path.anchor)
    parts = path.parts[1:]
    if not parts:
        return None
    for part in parts:
        current = current / part
        try:
            item_stat = os.lstat(current)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return missing_reason
            return lstat_failed_reason
        if stat.S_ISLNK(item_stat.st_mode):
            return symlink_reason
        if not stat.S_ISDIR(item_stat.st_mode):
            return not_directory_reason
    return None


def _stat_child_no_follow(dir_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        raise


def _candidate_record(
    alias: str,
    path: Path,
    name: str,
    category: str,
    file_type: str,
    entry_stat: os.stat_result,
) -> dict[str, object]:
    mode = stat.S_IMODE(entry_stat.st_mode)
    mtime = int(entry_stat.st_mtime)
    size = int(entry_stat.st_size)
    return {
        "source_alias": alias,
        "path": str(path),
        "category": category,
        "type": category,
        "file_type": file_type,
        "size": size,
        "mtime": mtime,
        "mode": f"{mode:04o}",
        "metadata_fingerprint": _metadata_fingerprint(
            alias=alias,
            name=name,
            category=category,
            file_type=file_type,
            size=size,
            mtime=mtime,
            mode=mode,
        ),
    }


def _metadata_fingerprint(
    *,
    alias: str,
    name: str,
    category: str,
    file_type: str,
    size: int,
    mtime: int,
    mode: int,
) -> str:
    payload = {
        "alias": alias,
        "category": category,
        "file_type": file_type,
        "mode": f"{mode:04o}",
        "mtime": mtime,
        "name": name,
        "size": size,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _category_for_name(name: str, file_type: str) -> str:
    lowered = name.lower()
    if file_type != "regular_file":
        return "other_candidate"
    if any(marker in lowered for marker in ("chat", "conversation", "export")) and lowered.endswith(
        (".json", ".jsonl", ".txt", ".zip")
    ):
        return "chat_export_candidate"
    if lowered.endswith((".sqlite", ".sqlite3", ".db")):
        return "memory_database_candidate"
    if "manifest" in lowered or lowered.endswith((".yaml", ".yml", ".toml")):
        return "manifest_candidate"
    if any(marker in lowered for marker in ("handoff", "brief", "summary", "packet")):
        return "project_handoff_candidate"
    if lowered.endswith((".md", ".txt", ".pdf", ".doc", ".docx", ".rtf")):
        return "document_candidate"
    return "other_candidate"


def _file_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular_file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "other"


def _empty_counts(*, selected_count: int) -> dict[str, int]:
    counts = {
        "selected_root_count": selected_count,
        "root_readable_count": 0,
        "root_unreadable_count": 0,
        "scanned_entry_count": 0,
        "candidate_count": 0,
        "excluded_secret_like_count": 0,
        "excluded_infrastructure_cache_name_count": 0,
        "symlink_count": 0,
        "truncated_root_count": 0,
    }
    for category in CANDIDATE_CATEGORIES:
        counts[f"{category}_count"] = 0
    return counts


def _blocked(
    reason: str,
    *,
    selected_count: int,
    counts: dict[str, int] | None = None,
) -> InventoryResult:
    safe_counts = counts or _empty_counts(selected_count=selected_count)
    lines = _public_lines(
        "BLOCKED",
        "not_met",
        safe_counts,
        report_sha="none",
        reasons=(reason,),
    )
    return InventoryResult(status="BLOCKED", success_criteria="not_met", lines=lines)


def _public_lines(
    status: str,
    success_criteria: str,
    counts: Mapping[str, int],
    *,
    report_sha: str,
    reasons: tuple[str, ...],
) -> list[str]:
    lines = [
        f"schema={SCHEMA_VERSION}",
        f"task_id={TASK_ID}",
        f"status={status}",
        f"reason={reasons[0]}",
        f"selected_root_count={counts['selected_root_count']}",
        f"root_readable_count={counts['root_readable_count']}",
        f"root_unreadable_count={counts['root_unreadable_count']}",
        f"scanned_entry_count={counts['scanned_entry_count']}",
        f"candidate_count={counts['candidate_count']}",
    ]
    for category in CANDIDATE_CATEGORIES:
        lines.append(f"{category}_count={counts[f'{category}_count']}")
    lines.extend(
        [
            f"excluded_secret_like_count={counts['excluded_secret_like_count']}",
            (
                "excluded_infrastructure_cache_name_count="
                f"{counts['excluded_infrastructure_cache_name_count']}"
            ),
            f"symlink_count={counts['symlink_count']}",
            f"truncated_root_count={counts['truncated_root_count']}",
            f"degraded={'true' if reasons != ('inventory_complete',) else 'false'}",
            f"truncation_evidence={'true' if counts['truncated_root_count'] else 'false'}",
            f"private_report_sha256={report_sha}",
            "content_files_read=false",
            "symlink_traversal=false",
            "runtime_private_action=false",
            "public_safe_report_ok=true",
            f"success_criteria={success_criteria}",
        ]
    )
    return lines


def _degraded_reasons(roots: list[dict[str, Any]], counts: Mapping[str, int]) -> tuple[str, ...]:
    reasons: list[str] = []
    if any(root["state"] == "unreadable" for root in roots):
        reasons.append("partial_unreadable_roots")
    if counts["truncated_root_count"]:
        reasons.append("truncated")
    return tuple(reasons)


def _mark_truncated(record: dict[str, Any], counts: dict[str, int], reason: str) -> None:
    if not record["truncated"]:
        record["truncated"] = True
        counts["truncated_root_count"] += 1
    if reason not in record["truncation_reasons"]:
        record["truncation_reasons"].append(reason)


def _timed_out(start: float, clock: Callable[[], float], timeout_seconds: int) -> bool:
    return (clock() - start) >= timeout_seconds


def _write_private_report(private_root: Path, payload: Mapping[str, object]) -> tuple[Path, str]:
    review_dir = private_root / "phase-a-review"
    dir_fd = _ensure_private_review_dir(private_root, review_dir)
    report_body = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    report_sha = hashlib.sha256(report_body).hexdigest()
    wrapper = {
        "schema": "skeleton.private_memory_source_inventory.private_report_wrapper.v1",
        "payload_sha256": report_sha,
        "payload": payload,
    }
    final_body = json.dumps(wrapper, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    final_sha = hashlib.sha256(final_body).hexdigest()
    filename = f"private-memory-phase-a-inventory-{int(time.time())}-{final_sha[:16]}.json"
    target = review_dir / filename
    temp_name = f".{filename}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        target_stat = _stat_child_no_follow(dir_fd, filename)
        if target_stat is not None:
            if stat.S_ISLNK(target_stat.st_mode):
                raise RuntimeError("report_target_symlink")
            if not stat.S_ISREG(target_stat.st_mode):
                raise RuntimeError("report_target_not_regular")
        temp_stat = _stat_child_no_follow(dir_fd, temp_name)
        if temp_stat is not None:
            if stat.S_ISLNK(temp_stat.st_mode):
                raise RuntimeError("report_temp_symlink")
            raise RuntimeError("report_temp_exists")
        fd = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(final_body)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            raise
        os.replace(temp_name, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        if os.chmod in os.supports_dir_fd and os.chmod in os.supports_follow_symlinks:
            os.chmod(filename, 0o600, dir_fd=dir_fd, follow_symlinks=False)
        elif os.chmod in os.supports_dir_fd:
            os.chmod(filename, 0o600, dir_fd=dir_fd)
        else:
            os.chmod(target, 0o600)
        if hasattr(os, "fsync"):
            os.fsync(dir_fd)
    except Exception:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(dir_fd)
    return target, final_sha


def _ensure_private_review_dir(private_root: Path, review_dir: Path) -> int:
    private_root_reason = _validate_existing_directory_components(
        private_root,
        missing_reason="private_root_missing",
        symlink_reason="path_component_symlink",
        not_directory_reason="path_component_not_directory",
        lstat_failed_reason="path_component_lstat_failed",
    )
    if private_root_reason is not None:
        raise RuntimeError(private_root_reason)
    if review_dir.exists() or review_dir.is_symlink():
        review_stat = os.lstat(review_dir)
        if stat.S_ISLNK(review_stat.st_mode):
            raise RuntimeError("review_dir_symlink")
        if not stat.S_ISDIR(review_stat.st_mode):
            raise RuntimeError("review_dir_not_directory")
    else:
        review_dir.mkdir(mode=0o700)
    os.chmod(review_dir, 0o700)
    review_dir_reason = _validate_existing_directory_components(
        review_dir,
        missing_reason="review_dir_missing",
        symlink_reason="review_dir_symlink",
        not_directory_reason="review_dir_not_directory",
        lstat_failed_reason="path_component_lstat_failed",
    )
    if review_dir_reason is not None:
        raise RuntimeError(review_dir_reason)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(review_dir, flags)


def _extract_task_block(body: str) -> str | None:
    lines = body.splitlines()
    in_block = False
    collected: list[str] = []
    for line in lines:
        if not in_block and line.strip() == "```task":
            in_block = True
            continue
        if in_block and line.strip() == "```":
            return "\n".join(collected)
        if in_block:
            collected.append(line)
    return None
