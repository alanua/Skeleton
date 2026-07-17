from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextvars import ContextVar
import csv
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import sysconfig
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.audit_ledger import AuditLedger, validate_public_safe_payload
from core.aufmass_source_pack import validate_source_pack_manifest
from core.hermes_private_memory import (
    orient_hermes_private_memory,
    record_hermes_private_memory_note,
    write_hermes_private_memory_heartbeat,
)
from core.hermes_worker import run_hermes_memory_task_packet
from core.loop_controller import LoopPolicy
from core.loop_engine import LoopEngine
from core.loop_runner_adapter import run_loop_task_packet
from core.loop_state_store import LoopStateStore
from core.runner_diagnostic_executor import (
    MEMPALACE_SYNTHETIC_BENCHMARK_SCHEMA,
    MEMPALACE_SYNTHETIC_BENCHMARK_TIMEOUT_SECONDS,
    MEMPALACE_SYNTHETIC_NAMESPACE,
    MEMPALACE_SYNTHETIC_PRIVATE_MARKERS,
    MEMPALACE_SYNTHETIC_PROJECT_ID,
    MEMPALACE_SYNTHETIC_REQUIRED_STABLE_REASONS,
    execute_mempalace_synthetic_runtime_smoke as _execute_mempalace_synthetic_runtime_smoke,
    mempalace_benchmark_output_has_private_marker as _executor_mempalace_benchmark_output_has_private_marker,
    parse_mempalace_benchmark_json as _executor_parse_mempalace_benchmark_json,
    reject_mempalace_runtime_smoke_issue_input as _executor_reject_mempalace_runtime_smoke_issue_input,
    validate_mempalace_benchmark_report as _executor_validate_mempalace_benchmark_report,
)
from core.runner_shadow_integration import (
    MAINTENANCE_TASK_KIND_BY_ID as SHADOW_MAINTENANCE_TASK_KIND_BY_ID,
    RunnerShadowReceipt,
    RunnerShadowCompatibilityBindings,
    blocked_shadow_receipt,
    evaluate_shadow_from_normalized_metadata,
)
from core.runner_loop_control_executor import (
    LOOP_ENGINE_PACKET,
    LOOP_STATE_DB_ENV,
    execute_loop_engine_packet as _execute_loop_engine_packet,
    loop_receipt_report as _executor_loop_receipt_report,
    loop_receipt_status_line as _executor_loop_receipt_status_line,
    loop_state_db_path as _executor_loop_state_db_path,
    loop_task_packet_from_body as _executor_loop_task_packet_from_body,
)
from core.private_memory import healthcheck_private_memory, write_public_heartbeat
from core.project_tree import get_project, get_project_by_repo, load_project_tree
from core.runner_child_environment import sanitize_codegen_child_environment
from core.runner_retry_policy import (
    BLOCK_REPEATED_REASON,
    NEEDS_OPERATOR,
    ROUTE_CODE_GENERATION,
    ROUTE_PUBLISH_ONLY,
    ROUTE_RUNTIME_ONLY,
    RetryCondition,
    RetryDecision,
    append_retry_fields,
    evaluate_retry_policy,
    expected_output_validation,
    extract_retry_override,
    one_time_override_hash,
    parse_prior_blocked_reports,
)
from core.runner_private_memory_executor import (
    HERMES_MEMORY_GATEWAY_SMOKE_LOOKUP_KEY,
    HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE,
    HERMES_MEMORY_GATEWAY_SMOKE_OPERATIONS,
    HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID,
    MEMORY_GATEWAY_CONTRACT_VERSION,
    MEMORY_GATEWAY_RESPONSE_SCHEMA,
    MemoryGateway,
    capability_token,
    execute_hermes_memory_gateway_smoke as _execute_hermes_memory_gateway_smoke,
    hermes_memory_gateway_smoke_bounded_count as _executor_hermes_memory_gateway_smoke_bounded_count,
    hermes_memory_gateway_smoke_bounded_ref as _executor_hermes_memory_gateway_smoke_bounded_ref,
    hermes_memory_gateway_smoke_exact_summary as _executor_hermes_memory_gateway_smoke_exact_summary,
    hermes_memory_gateway_smoke_failure as _executor_hermes_memory_gateway_smoke_failure,
    hermes_memory_gateway_smoke_packet as _executor_hermes_memory_gateway_smoke_packet,
    hermes_memory_gateway_smoke_proposal as _executor_hermes_memory_gateway_smoke_proposal,
    hermes_memory_gateway_smoke_report_lines as _executor_hermes_memory_gateway_smoke_report_lines,
    hermes_memory_gateway_smoke_run_and_validate as _executor_hermes_memory_gateway_smoke_run_and_validate,
    hermes_memory_gateway_smoke_validate_common as _executor_hermes_memory_gateway_smoke_validate_common,
    hermes_memory_gateway_smoke_validate_isolation as _executor_hermes_memory_gateway_smoke_validate_isolation,
    hermes_memory_gateway_smoke_validate_payload as _executor_hermes_memory_gateway_smoke_validate_payload,
)
from core.skeleton_memory import SkeletonMemory
from core.telegram_approval_buttons import build_pr_ready_card_payload


QUEUE_REPOSITORY = "alanua/Skeleton"
REPO = QUEUE_REPOSITORY
RUNNER_GITHUB_ACTOR_ENV = "SKELETON_RUNNER_GITHUB_ACTOR"
RUNNER_SHADOW_MODE_ENV = "SKELETON_RUNNER_SHADOW_MODE"
LAST_RUNNER_SHADOW_RECEIPT: dict[str, object] | None = None


def trusted_runner_comment_authors() -> frozenset[str]:
    actors = {
        QUEUE_REPOSITORY.split("/", 1)[0].lower(),
        "github-actions[bot]",
    }
    configured = os.environ.get(RUNNER_GITHUB_ACTOR_ENV, "").strip().lower()
    if configured:
        actors.add(configured)
    return frozenset(actors)


LABEL_READY = "runner:ready"
LABEL_RUNNING = "runner:running"
LABEL_DONE = "runner:done"
LABEL_BLOCKED = "runner:blocked"
RUNNER_LANE_LABELS = {
    "default": "runner:lane:default",
    "lane-1": "runner:lane:lane-1",
    "lane-2": "runner:lane:lane-2",
}
RUNNER_LANE_LABEL_DESCRIPTIONS = {
    "default": "Visible default Runner lane marker",
    "lane-1": "Visible Runner lane-1 marker",
    "lane-2": "Visible Runner lane-2 marker",
}
FINAL_LABELS_BY_STATUS = {
    "DONE": LABEL_DONE,
    "BLOCKED": LABEL_BLOCKED,
}
POLL_INTERVAL = 60
DEFAULT_WORKDIR = Path(__file__).resolve().parents[1]
DEFAULT_WORKTREE_ROOT = Path("/home/agent/agent-dev/worktrees/skeleton")
PROJECT_TREE_PATH = ROOT / "PROJECT_TREE.yaml"
MAX_COMMENT_LENGTH = 60000
RUNTIME_ARTIFACTS = (
    "core/__pycache__",
    "tests/__pycache__",
    "scripts/__pycache__",
    ".codex",
)
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT_SECONDS = 10
TELEGRAM_CALLBACK_DATA_LIMIT = 64
TELEGRAM_CALLBACK_HMAC_ENV = "SKELETON_TG_CALLBACK_HMAC_SECRET"
CODEX_MODEL_ENV = "SKELETON_CODEX_MODEL"
TELEGRAM_CARD_TEST_SUMMARY = "Runner pytest completed before draft PR creation."
TELEGRAM_CARD_RISK_SUMMARY = "Review the changed-file list before approval."
TELEGRAM_PR_READY_BUTTON_LABELS = {
    "approve": "Схвалити",
    "reject": "Відхилити",
    "details": "Деталі",
    "open_pr": "Відкрити PR",
}
RUNTIME_MAINTENANCE_MODE = "RUNTIME_MAINTENANCE_TASK"
SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME = "sync_telegram_callback_poller_runtime"
ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG = "ensure_telegram_callback_local_config"
CHECK_PROJECT_CHECKOUT = "check_project_checkout"
CHECK_SKELETON_FRESHNESS = "check_skeleton_freshness"
RUNTIME_SYNC_MAIN = "runtime_sync_main"
RECOVER_SKELETON_CHECKOUT = "recover_skeleton_checkout"
ENSURE_PROJECT_CHECKOUT = "ensure_project_checkout"
VALIDATE_PR_BRANCH = "validate_pr_branch"
PREFLIGHT_PR_REFRESH = "preflight_pr_refresh"
HERMES_WORKER_PREFLIGHT = "hermes_worker_preflight"
HERMES_MEMORY_GATEWAY_SMOKE = "hermes_memory_gateway_smoke"
MEMPALACE_SYNTHETIC_RUNTIME_SMOKE = "mempalace_synthetic_runtime_smoke"
PRIVATE_MEMORY_HEALTHCHECK = "private_memory_healthcheck"
HERMES_PRIVATE_MEMORY_BRIDGE_CHECK = "hermes_private_memory_bridge_check"
INSTALL_GRAPHIFY_RUNTIME = "install_graphify_runtime"
PREPARE_AUFMASS_PRIVATE_RUNTIME = "prepare_aufmass_private_runtime"
RUN_AUFMASS_PRIVATE_DXF_REVIEW = "run_aufmass_private_dxf_review"
SUMMARIZE_AUFMASS_PRIVATE_REVIEW = "summarize_aufmass_private_review"
BUILD_AUFMASS_PRIVATE_SHORTLIST = "build_aufmass_private_shortlist"
BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE = "build_aufmass_private_area_schedule"
INSPECT_PR_MERGEABILITY = "inspect_pr_mergeability"
BACKFILL_SKELETON_MEMORY_RECENT = "backfill_skeleton_memory_recent"
INSPECT_ISSUE_WORKTREE_FOR_PUBLISH = "inspect_issue_worktree_for_publish"
PUBLISH_ISSUE_WORKTREE_PR = "publish_issue_worktree_pr"
PUBLISH_EXISTING_ISSUE_WORKTREE = "publish_existing_issue_worktree"
PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR = "publish_issue_worktree_to_existing_pr"
OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR = "overlay_registered_worktree_to_existing_pr"
PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR = "publish_target_project_issue_worktree_pr"
PUBLISH_CONTAINER_VALIDATION_WORKTREE = "publish_container_validation_worktree"
REPAIR_PROTECTED_EXACT_SOURCE_WORKTREE = "repair_protected_exact_source_worktree"
QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES = (
    "quarantine_stale_clean_skeleton_worktrees"
)
HOME_EDGE_01_READ_ONLY_DIAGNOSTIC = "home_edge_01_read_only_diagnostic"
HOME_EDGE_01_LAN_INVENTORY_READ_ONLY = "home_edge_01_lan_inventory_read_only"
RUNTIME_MAINTENANCE_TASK_IDS = frozenset(
    (
        SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
        ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG,
        CHECK_PROJECT_CHECKOUT,
        CHECK_SKELETON_FRESHNESS,
        RUNTIME_SYNC_MAIN,
        RECOVER_SKELETON_CHECKOUT,
        ENSURE_PROJECT_CHECKOUT,
        VALIDATE_PR_BRANCH,
        PREFLIGHT_PR_REFRESH,
        HERMES_WORKER_PREFLIGHT,
        HERMES_MEMORY_GATEWAY_SMOKE,
        LOOP_ENGINE_PACKET,
        MEMPALACE_SYNTHETIC_RUNTIME_SMOKE,
        PRIVATE_MEMORY_HEALTHCHECK,
        HERMES_PRIVATE_MEMORY_BRIDGE_CHECK,
        INSTALL_GRAPHIFY_RUNTIME,
        PREPARE_AUFMASS_PRIVATE_RUNTIME,
        RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        SUMMARIZE_AUFMASS_PRIVATE_REVIEW,
        BUILD_AUFMASS_PRIVATE_SHORTLIST,
        BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE,
        INSPECT_PR_MERGEABILITY,
        BACKFILL_SKELETON_MEMORY_RECENT,
        INSPECT_ISSUE_WORKTREE_FOR_PUBLISH,
        PUBLISH_ISSUE_WORKTREE_PR,
        PUBLISH_EXISTING_ISSUE_WORKTREE,
        PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR,
        OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR,
        PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR,
        PUBLISH_CONTAINER_VALIDATION_WORKTREE,
        REPAIR_PROTECTED_EXACT_SOURCE_WORKTREE,
        QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES,
        HOME_EDGE_01_READ_ONLY_DIAGNOSTIC,
        HOME_EDGE_01_LAN_INVENTORY_READ_ONLY,
    )
)
PUBLISH_ONLY_MAINTENANCE_TASK_IDS = frozenset(
    (
        INSPECT_ISSUE_WORKTREE_FOR_PUBLISH,
        PUBLISH_ISSUE_WORKTREE_PR,
        PUBLISH_EXISTING_ISSUE_WORKTREE,
        PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR,
        OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR,
        PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR,
        PUBLISH_CONTAINER_VALIDATION_WORKTREE,
    )
)
CONTAINER_VALIDATION_SOURCE_ISSUE = 1667
CONTAINER_VALIDATION_BRANCH = "runner/issue-1667"
CONTAINER_VALIDATION_BASE_BRANCH = "main"
CONTAINER_VALIDATION_WORKTREE_ID = "issue-1667"
CONTAINER_VALIDATION_PUBLISH_FILES = (
    ".github/workflows/container-package-validation.yml",
    "docs/CONTAINER_PACKAGE_VALIDATION.md",
    "tests/test_container_package_validation_workflow.py",
)
AUFMASS_PRIVATE_PROJECT_ID = "aufmass_private"
AUFMASS_PRIVATE_REGISTERED_REPO = "private/aufmass"
AUFMASS_PRIVATE_SOURCE_PACK_MANIFEST = "source_pack_manifest.json"
AUFMASS_PRIVATE_AUTOMATION_REGISTRY = "automation_registry.private.json"
AUFMASS_PRIVATE_AUTOMATION_REGISTRY_SCHEMA = (
    "skeleton.aufmass_private_automation_registry.v1"
)
AUFMASS_PRIVATE_REQUIRED_MODULES = (
    "core.aufmass_engine",
    "core.aufmass_exporter",
    "core.aufmass_manual_adapter",
    "core.aufmass_source_pack",
    "scripts.aufmass_private_pilot_run",
)
AUFMASS_PRIVATE_OPTIONAL_DXF_MODULE = "ezdxf"
RUNNER_PROJECT_CHECKOUT_BASE = Path("/home/agent/agent-dev")
RUNNER_ALLOWED_TARGET_PATH_BASES = (
    RUNNER_PROJECT_CHECKOUT_BASE / "repos",
    RUNNER_PROJECT_CHECKOUT_BASE / "worktrees",
)
PR_BRANCH_VALIDATION_WORKTREE_DIR = "validate-pr-branch"
PR_BRANCH_VALIDATION_PROFILES = {
    "full_pytest": (("python3", "-m", "pytest", "-q"),),
    "runner_exact_base": (
        ("python3", "-m", "pytest", "-q", "tests/test_runner_poll_github_tasks.py"),
        ("python3", "-m", "pytest", "-q"),
        (
            "python3",
            "-m",
            "py_compile",
            "scripts/runner_poll_github_tasks.py",
            "tests/test_runner_poll_github_tasks.py",
        ),
        ("git", "diff", "--check", "{validated_base_sha}...HEAD"),
    ),
    "knowledge_intake": (
        ("python3", "-m", "pytest", "-q", "tests/test_knowledge_intake.py"),
        ("python3", "-m", "pytest", "-q"),
    ),
    "time_ledger_stage1": (
        ("python3", "-m", "pytest", "-q", "tests/test_time_ledger.py"),
        (
            "python3",
            "-m",
            "py_compile",
            "api/services/time_ledger.py",
            "api/services/arbzg_policy.py",
            "tests/test_time_ledger.py",
        ),
    ),
}
VALIDATION_FAILED_OUTPUT_LIMIT = 4000
VALIDATION_FAILED_OUTPUT_TRUNCATED_MARKER = (
    "[Runner validation output truncated to 4000 characters.]"
)
QUARANTINE_REMOVE_FAILED_OUTPUT_LIMIT = 1200
QUARANTINE_REMOVE_FAILED_OUTPUT_TRUNCATED_MARKER = (
    "[Runner quarantine remove output truncated to 1200 characters.]"
)
PROTECTED_SOURCE_FETCH_TIMEOUT_SECONDS = 120
PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS = 30
TELEGRAM_APPROVED_PR_MERGE_MODE = "TELEGRAM_APPROVED_PR_MERGE"
TELEGRAM_APPROVED_PR_MERGE_ACTION = "squash"
TELEGRAM_CALLBACK_POLLER_SERVICE = "skeleton-telegram-callback-poll.service"
TELEGRAM_CALLBACK_POLLER_TIMER = "skeleton-telegram-callback-poll.timer"
TELEGRAM_CALLBACK_LOCAL_CONFIG = "/etc/skeleton-runner.env"
TELEGRAM_CALLBACK_POLLER_RUNTIME_FILES = (
    "scripts/telegram_callback_poller.py",
    f"scripts/{TELEGRAM_CALLBACK_POLLER_SERVICE}",
    f"scripts/{TELEGRAM_CALLBACK_POLLER_TIMER}",
)

_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_REGISTERED_OVERLAY_PUBLIC_REST_REPO = "alanua/Skeleton"
_REGISTERED_OVERLAY_PUBLIC_REST_HOST = "api.github.com"
_REGISTERED_OVERLAY_PUBLIC_REST_TIMEOUT_SECONDS = 8
_REGISTERED_OVERLAY_PUBLIC_REST_PR_BYTES = 262_144
_REGISTERED_OVERLAY_PUBLIC_REST_FILES_BYTES = 262_144
_REGISTERED_OVERLAY_PUBLIC_REST_FILE_PAGE_CAP = 10
_REGISTERED_OVERLAY_PUBLIC_REST_FILE_COUNT_CAP = 1_000
_REGISTERED_OVERLAY_PUBLIC_REST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "skeleton-runner-registered-overlay",
}
_CALLBACK_DIGEST_RE = re.compile(r"^[0-9a-f]{12}$")
_BLOCKED_OUTPUT_MARKERS = (
    "BLOCKED",
    "Blocked:",
    "missing capability",
    "wrong worktree",
    "not target repo",
    "writer unavailable",
    "cancelled",
    "no build files",
    "PlatformIO not available",
    "no firmware",
    "assigned worktree is not target",
)
_BLOCKED_OUTPUT_MARKER_RES = tuple(
    re.compile(r"^\s*BLOCKED\s*:", re.IGNORECASE | re.MULTILINE)
    if marker == "BLOCKED"
    else re.compile(rf"(?<!\w){re.escape(marker)}(?!\w)", re.IGNORECASE)
    for marker in _BLOCKED_OUTPUT_MARKERS
)
_FINAL_STATUS_LINE_RE = re.compile(
    r"^\s*(DONE|BLOCKED|NEEDS_OPERATOR)\b:?", re.IGNORECASE
)
_FINAL_STATUS_DELIVERY_LINE_RE = re.compile(
    r"^\s*(DONE|BLOCKED|NEEDS_OPERATOR)\s*:", re.IGNORECASE
)
_FINAL_RESULT_LINE_RE = re.compile(
    r"^\s*RESULT:\s*(?P<result>DONE|BLOCKED|NEEDS_OPERATOR)\b", re.IGNORECASE
)
_REPORT_OPERATOR_REQUIRED_RE = re.compile(
    r"^\s*(?:RESULT:\s*)?NEEDS_OPERATOR\b:?", re.IGNORECASE | re.MULTILINE
)
_LOCAL_WORKTREE_DONE_PREFIX = (
    "DONE: Codex completed successfully in the local target-project worktree."
)
_LOCAL_WORKTREE_BOUNDED_FINALIZATION_EVIDENCE = (
    "Local worktree bounded finalization: success"
)
_CODEX_TRANSCRIPT_TAIL_RE = re.compile(
    r"(?m)^(?:Reading additional input from stdin\.\.\.|OpenAI Codex v)"
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ENV_ASSIGNMENT_LINE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,80}=.*$")
_SENSITIVE_OUTPUT_VALUE_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASS|KEY|CREDENTIAL|AUTH)"
    r"[A-Z0-9_]*)\s*=\s*([^\s]+)"
)
_MAINTENANCE_TOP_LEVEL_STATUS_RE = re.compile(
    r"^\s*(DONE|BLOCKED|NEEDS_OPERATOR)\b:?", re.IGNORECASE
)
_MAINTENANCE_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:token|secret|api_key|access_key|password|credential|private_key|bearer|authorization)"
)
_MAINTENANCE_ASSIGNMENT_KEY_RE = re.compile(
    r"^[a-z][a-z0-9_]{0,80}$"
)
_MAINTENANCE_SYMBOLIC_VALUE_RE = re.compile(
    r"^[A-Za-z0-9._:+,@/\[\]{}()#-]+$"
)
_MAINTENANCE_PR_URL_KEYS = frozenset(
    {"draft_pr_url", "existing_pr_url", "pr_url"}
)
_MAINTENANCE_FORBIDDEN_STATUS_KEYS = frozenset(
    {
        "checkout_path",
        "exception",
        "failed_command",
        "issue_worktree",
        "private_workspace",
        "raw_output",
        "stderr",
        "stdout",
        "traceback",
        "worktree_root",
    }
)
_MAINTENANCE_PUBLIC_STATUS_KEYS = frozenset(
    {
        "accepted",
        "action",
        "ahead_by",
        "allowed_files_count",
        "allowed_untracked_files",
        "allowed_untracked_files_count",
        "approved_override_hash",
        "approval_status",
        "approved_head_sha",
        "artifact_count",
        "base_branch",
        "base_ref_oid",
        "base_ref",
        "base_sha",
        "behind_by",
        "blocked_write_status",
        "branch",
        "build_ms",
        "canon_note",
        "changed_file",
        "changed_file_count",
        "changed_files",
        "changed_files_count",
        "changed_tracked_files",
        "changed_tracked_files_count",
        "check",
        "checkout_head_sha",
        "checkout_path",
        "checkout_sync_state",
        "context_hash",
        "command_unavailable_reason",
        "compare_ahead_by",
        "compare_behind_by",
        "compare_state",
        "compare_status",
        "canonical_write_enabled",
        "constructed_head_sha",
        "current_branch",
        "decision_records_skipped",
        "decision_records_written",
        "decision",
        "diagnostic_count",
        "device_count",
        "responsive_count",
        "gateway_presence",
        "gateway_status",
        "service_category_counts",
        "risk_flags",
        "usb_modem_health_requirement",
        "internet_path_expectation",
        "gateway_modem_internals",
        "private_details",
        "disk_bytes",
        "draft",
        "draft_pr",
        "draft_pr_url",
        "dxf_source_count",
        "error_class",
        "event",
        "external_side_effects_executed",
        "existing_pr_lookup",
        "existing_pr_url",
        "expected_branch",
        "expected_head_sha",
        "expected_pr_head_branch",
        "expected_pr_head_sha",
        "expected_source_branch",
        "expected_source_sha",
        "expected_target_branch",
        "expected_target_head_sha",
        "exit_code",
        "explicit_recovery_route",
        "file_on_main",
        "final_clean_state",
        "files_on_main_count",
        "gated_heartbeat_status",
        "gated_note_status",
        "git_worktree_list_registers_path",
        "git_worktree_list_status",
        "github_main_sha",
        "github_main_source_of_truth",
        "graphify_version",
        "head_branch",
        "head_ref",
        "head_repository",
        "head_sha",
        "hermes_bridge_status",
        "hermes_gateway_contract",
        "hermes_memory_operation_count",
        "hermes_memory_smoke_status",
        "host_id_sha256_12",
        "input_row_count",
        "input_table_count",
        "installed_skill_platform_count",
        "inventory_schema",
        "issue_number",
        "issue_worktree",
        "issue_worktree_id",
        "kernel_release",
        "ledger_events_written",
        "listed_worktrees_count",
        "machine",
        "maintenance_task_id",
        "managed_version_marker_count",
        "model_credentials_used",
        "merge_action",
        "mergeable",
        "mergeable_state",
        "memory_events_existing",
        "memory_events_written",
        "missing_file",
        "missing_dependency_module",
        "mode",
        "model_credentials_removed_from_smoke",
        "mutation_mode",
        "network_disabled",
        "network_provider_enabled",
        "next_action",
        "next_operator_action",
        "open_issues_count",
        "open_pull_requests_count",
        "orient_status",
        "output_branch",
        "pilot_mode",
        "pilot_summary_schema",
        "ports_disabled",
        "ports_enabled",
        "post_push_pr_metadata_source",
        "pr_number",
        "pr_metadata_source",
        "pr_state",
        "pr_title",
        "pr_url",
        "publish_override_hash",
        "private_memory_db_configured",
        "private_memory_db_openable",
        "private_memory_heartbeat_ok",
        "private_memory_integrity_ok",
        "private_memory_schema_present",
        "private_memory_status",
        "private_memory_writable_when_requested",
        "private_memory_write_requested",
        "private_workspace",
        "profile_backup_item_count",
        "profile_backup_private",
        "profile_backup_status",
        "project_id",
        "project_state_existing",
        "project_state_written",
        "pre_push_pr_changed_files_count",
        "pre_push_pr_file_count",
        "public_safe",
        "protected_worktrees_count",
        "pushed_head_sha",
        "public_safe_report_ok",
        "quality_score",
        "quality_threshold",
        "pull_request",
        "recovery_packet",
        "python_version",
        "reason",
        "recovery_artifact_status",
        "recovery_ref_status",
        "recovery_restore_status",
        "recovery_stash_status",
        "recovery_snapshot_status",
        "reset_status",
        "removed_worktrees_count",
        "report_drawings",
        "report_mode",
        "report_private_paths",
        "report_quantities",
        "review_table_count",
        "runtime_smoke_check_count",
        "runtime_smoke_decision",
        "runtime_smoke_stable_reason",
        "ram_bytes",
        "repository",
        "rollback_status",
        "room_area_row_count",
        "row_count",
        "run_id",
        "runner_root_exists",
        "runner_status",
        "selected_source_count",
        "services_disabled",
        "services_enabled",
        "shortlist_row_count",
        "skipped_worktrees_count",
        "source_issue",
        "source_issue_number",
        "source_ref",
        "source_pack_error_count",
        "source_pack_id",
        "source_pack_warning_count",
        "source_token_count",
        "sourcepack_note",
        "schema",
        "status",
        "status_token",
        "status_count_approved",
        "status_count_needs_review",
        "step",
        "success_criteria",
        "synthetic_corpus_status",
        "live_private_ingestion",
        "loop_state",
        "synthetic_graph_edge_count",
        "synthetic_graph_node_count",
        "synthetic_smoke_timeout_seconds",
        "system",
        "target_project",
        "task_id",
        "target_project_route",
        "target_repository",
        "test_summary",
        "tool_codex",
        "tool_gh",
        "tool_git",
        "tool_python3",
        "tracked_files_match_allowlist",
        "target_branch",
        "target_head_sha",
        "unexpected_untracked_files",
        "unexpected_untracked_files_count",
        "new_pr_changed_files_count",
        "post_push_pr_changed_files_count",
        "validated_publish_files",
        "validated_publish_files_count",
        "validation_base_ref",
        "validation_base_sha",
        "validation_changed_file",
        "validation_changed_files_count",
        "validation_checkout_head_sha",
        "validation_command_index",
        "validation_command_text",
        "validation_error_summary",
        "validation_failure_phase",
        "validation_final_status",
        "validation_failing_node",
        "validation_initial_status",
        "validation_output_tail",
        "version",
        "validation_profile",
        "validation_state",
        "validation_pytest_totals",
        "validation_pytest_version",
        "validation_real_writable_git_worktree",
        "wall_area_row_count",
        "warning_count",
        "worktree",
        "worktree_id",
        "worktree_ids",
        "worktree_root",
    }
)
RUNNER_MEMORY_DB_ENV = "SKELETON_RUNNER_MEMORY_DB"
RUNNER_MEMORY_LEDGER_ENV = "SKELETON_RUNNER_MEMORY_LEDGER"
RUNNER_MEMORY_DIR_ENV = "SKELETON_RUNNER_MEMORY_DIR"
RUNNER_MEMORY_WARNING = "Memory warning: Runner memory write failed."
_PUBLIC_GITHUB_PR_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9]\d*/?$"
)
_SAFE_CHANGED_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]*$")
_PYTEST_SUMMARY_LINE_RE = re.compile(
    r"(?i)\b(?:\d+\s+(?:passed|failed|skipped|xfailed|xpassed|error|errors|warnings?)"
    r"|no tests ran|failed|passed)\b"
)
_SAFE_CODEX_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_TASK_FENCE_OPEN_LINE_RE = re.compile(r"^[ \t]*```task[ \t]*$")
_FENCE_CLOSE_LINE_RE = re.compile(r"^[ \t]*```[ \t]*$")
_AUFMASS_PRIVATE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,80}$")


@dataclass(frozen=True)
class RunnerLane:
    name: str


@dataclass(frozen=True)
class CodexTaskResult:
    status: str
    marker: str | None = None


DEFAULT_RUNNER_LANE = RunnerLane("default")
ALLOWED_RUNNER_LANES = frozenset(RUNNER_LANE_LABELS)


def load_runner_project_tree() -> dict[str, Any]:
    return load_project_tree(PROJECT_TREE_PATH)


def allowed_target_repositories() -> frozenset[str]:
    project_tree = load_runner_project_tree()
    return frozenset(
        project["repo"]
        for project in project_tree["projects"].values()
        if project["public"] is True
    )


ALLOWED_TARGET_REPOSITORIES = allowed_target_repositories()


def allowed_target_projects() -> frozenset[str]:
    project_tree = load_runner_project_tree()
    return frozenset(
        project_id
        for project_id, project in project_tree["projects"].items()
        if project["public"] is True
    )


ALLOWED_TARGET_PROJECTS = allowed_target_projects()
TARGET_REPOSITORY_METADATA_FIELDS = (
    "Target Repository",
    "Selected Repository",
    "Repo",
)


@dataclass(frozen=True)
class RunnerTask:
    content: str
    lane: RunnerLane = DEFAULT_RUNNER_LANE
    has_lane_metadata: bool = False
    target_project: str = "skeleton"
    has_target_project_metadata: bool = False
    target_repository: str = QUEUE_REPOSITORY
    has_target_repository_metadata: bool = False


@dataclass(frozen=True)
class TelegramApprovedPrMergeRequest:
    pr_number: int
    approved_head_sha: str
    callback_digest: str
    action: str = TELEGRAM_APPROVED_PR_MERGE_ACTION


@dataclass(frozen=True)
class RegisteredProjectCheckout:
    target_project: str
    repo: str
    checkout_path_text: str
    checkout_path: Path
    status_lines: list[str]


@dataclass(frozen=True)
class RunnerMemoryConfig:
    db_path: Path
    ledger_path: Path


@dataclass(frozen=True)
class PrBranchValidationRequest:
    repository: str
    pr_number: int
    expected_head_sha: str | None
    expected_base_sha: str
    profile: str


@dataclass(frozen=True)
class PreflightPrRefreshRequest:
    pr_number: int
    expected_head_sha: str | None


@dataclass(frozen=True)
class PrMergeabilityInspectionRequest:
    pr_number: int
    expected_head_sha: str | None


@dataclass(frozen=True)
class IssueWorktreePublishInspectionRequest:
    repository: str
    source_issue: int
    expected_branch: str
    allowed_files: frozenset[str]
    pr_title: str
    base_branch: str = "main"
    draft_pr: bool = True
    target_project: str = "skeleton"
    worktree_root: Path | None = None
    target_project_route: bool = False


@dataclass(frozen=True)
class ProtectedExactSourceWorktreeRepairRequest:
    target_repository: str
    source_issue: int
    output_branch: str
    source_ref: str
    expected_source_sha: str


@dataclass(frozen=True)
class IssueWorktreeExistingPrPublishRequest:
    repository: str
    source_issue: int
    expected_source_branch: str
    pr_number: int
    expected_pr_head_sha: str
    expected_pr_head_branch: str
    allowed_files: frozenset[str]


@dataclass(frozen=True)
class RegisteredWorktreeOverlayPacket:
    packet_id: str
    source_issue: int
    source_branch: str
    pr_number: int
    target_branch: str
    target_head_sha: str
    allowed_files: tuple[str, ...]


@dataclass(frozen=True)
class RegisteredWorktreeOverlayRequest:
    packet: RegisteredWorktreeOverlayPacket
    operator_approval: str


@dataclass(frozen=True)
class RegisteredWorktreeOverlaySourceSpec:
    path: str
    blob_sha: str | None = None


@dataclass(frozen=True)
class ContainerValidationWorktreePublishRequest:
    repository: str
    source_issue: int
    expected_source_branch: str
    base_branch: str
    output_branch: str
    draft_pr: bool
    operator_approval: str


@dataclass(frozen=True)
class IssueWorktreePublishExistingPrLookup:
    pr_url: str | None
    reason: str


@dataclass(frozen=True)
class StaleCleanSkeletonWorktreeQuarantineRequest:
    worktree_ids: tuple[str, ...]
    protected_ids: frozenset[str]


@dataclass(frozen=True)
class AufmassPrivateAutomationRequest:
    source_pack_id: str
    mode: str = "dry-run"
    run_id: str | None = None


@dataclass(frozen=True)
class AufmassPrivateRegistryEntry:
    source_pack_id: str
    manifest_path: Path
    artifact_map_path: Path
    output_root: Path
    run_id: str


def truncate_comment(body: str) -> str:
    if len(body) <= MAX_COMMENT_LENGTH:
        return body
    suffix = "\n\n[Runner output truncated.]"
    return body[: MAX_COMMENT_LENGTH - len(suffix)] + suffix


def cleanup_runtime_artifacts(workdir: str | Path, *, strict: bool = False) -> None:
    root = Path(workdir)
    for relative_path in RUNTIME_ARTIFACTS:
        artifact = root / relative_path
        if artifact.is_dir():
            shutil.rmtree(artifact, ignore_errors=not strict)
        elif artifact.exists():
            artifact.unlink(missing_ok=True)


_RUN_COMMAND_ENV_OVERRIDE: ContextVar[Mapping[str, str] | None] = ContextVar(
    "_RUN_COMMAND_ENV_OVERRIDE",
    default=None,
)


def run_command(
    args: list[str],
    cwd: str | Path | None = None,
    *,
    timeout: int | None = None,
) -> tuple[int, str]:
    run_kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd is not None else None,
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    environment = _RUN_COMMAND_ENV_OVERRIDE.get()
    if environment is not None:
        run_kwargs["env"] = dict(environment)

    result = subprocess.run(args, **run_kwargs)
    return result.returncode, result.stdout + result.stderr


def _validation_command_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environment is None else environment
    return sanitize_codegen_child_environment(source)


def _run_validation_profile_command(
    args: list[str],
    cwd: str | Path,
) -> tuple[int, str]:
    token = _RUN_COMMAND_ENV_OVERRIDE.set(
        _validation_command_environment()
    )
    try:
        return run_command(args, cwd=cwd)
    finally:
        _RUN_COMMAND_ENV_OVERRIDE.reset(token)


def _run_finalization_validation_command(
    args: list[str],
    cwd: str | Path,
) -> tuple[int, str]:
    return _run_validation_profile_command(args, cwd=cwd)


def worktree_root() -> Path:
    configured_root = os.environ.get("SKELETON_WORKTREE_ROOT")
    if configured_root:
        return Path(configured_root).expanduser()
    return DEFAULT_WORKTREE_ROOT


def issue_worktree_path(issue_number: int) -> Path:
    return worktree_root() / f"issue-{issue_number}"


def target_repository_worktree_root(target_repository: str) -> Path:
    project = _project_for_target_repository(target_repository)
    if target_repository == QUEUE_REPOSITORY:
        return worktree_root()
    return _validated_registered_target_path(
        target_repository, "worktree_root", project["worktree_root"]
    )


def target_repository_checkout_path(target_repository: str) -> Path:
    project = _project_for_target_repository(target_repository)
    return _validated_registered_target_path(
        target_repository, "checkout_path", project["checkout_path"]
    )


def _project_for_target_repository(target_repository: str) -> dict[str, Any]:
    project_tree = load_runner_project_tree()
    projects = project_tree.get("projects")
    if isinstance(projects, dict):
        for project in projects.values():
            if isinstance(project, dict) and project.get("repo") == target_repository:
                if project.get("public") is not True:
                    break
                return project
    allowed = ", ".join(f"`{repo}`" for repo in sorted(ALLOWED_TARGET_REPOSITORIES))
    raise ValueError(
        f"Target repository `{target_repository}` is not allowlisted. Use {allowed}."
    )


def _path_is_under_allowed_target_base(path: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    for base in RUNNER_ALLOWED_TARGET_PATH_BASES:
        try:
            resolved_path.relative_to(base.resolve(strict=False))
        except ValueError:
            continue
        return True
    return False


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except ValueError:
        return False
    return True


def _validated_registered_target_path(
    target_repository: str, field: str, raw_path: object
) -> Path:
    path_text = str(raw_path)
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError(
            f"Registered {field} for `{target_repository}` must be absolute: {path_text}"
        )
    if any(part == ".." for part in path.parts):
        raise ValueError(
            f"Registered {field} for `{target_repository}` contains traversal: {path_text}"
        )
    if not _path_is_under_allowed_target_base(path):
        allowed = ", ".join(str(base) for base in RUNNER_ALLOWED_TARGET_PATH_BASES)
        raise ValueError(
            f"Registered {field} for `{target_repository}` is outside allowed Runner bases "
            f"({allowed}): {path_text}"
        )
    return path


def verify_target_repository_checkout(target_repository: str) -> str | None:
    try:
        target_repository_worktree_root(target_repository)
        checkout_path = target_repository_checkout_path(target_repository)
    except ValueError:
        return (
            "Target repository route is invalid:\n```\n"
            f"target_repository={target_repository}\n"
            f"reason=registered_target_path_invalid\n"
            "```"
        )
    status_lines = [f"target_repository={target_repository}"]
    if not checkout_path.exists():
        return "Target repository checkout is unavailable:\n```\n" + "\n".join(
            [*status_lines, "reason=checkout_path_missing"]
        ) + "\n```"
    if not (checkout_path / ".git").exists():
        return "Target repository checkout is unavailable:\n```\n" + "\n".join(
            [*status_lines, "reason=checkout_git_missing"]
        ) + "\n```"
    return None


def target_repository_issue_worktree_path(
    target_repository: str, issue_number: int
) -> Path:
    return target_repository_worktree_root(target_repository) / f"issue-{issue_number}"


def ensure_safe_target_repository_worktree_path(
    target_repository: str, path: str | Path
) -> Path:
    root = target_repository_worktree_root(target_repository).resolve()
    candidate = Path(path).expanduser().resolve()
    if candidate == root:
        raise ValueError(f"Refusing to use worktree root as issue worktree: {candidate}")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Refusing worktree path outside configured root {root}: {candidate}"
        ) from exc
    return candidate


def ensure_safe_worktree_path(path: str | Path) -> Path:
    return ensure_safe_target_repository_worktree_path(QUEUE_REPOSITORY, path)


def issue_branch(issue_number: int) -> str:
    return f"runner/issue-{issue_number}"


def format_command_output(command: list[str], output: str) -> str:
    return f"$ {' '.join(command)}\n{output}"


def prepare_issue_worktree(
    issue_number: int, coordinator_workdir: str | Path
) -> tuple[int, str, Path]:
    path = ensure_safe_worktree_path(issue_worktree_path(issue_number))
    return prepare_git_issue_worktree(issue_number, coordinator_workdir, path)


def prepare_target_repository_issue_worktree(
    target_repository: str, issue_number: int
) -> tuple[int, str, Path]:
    try:
        path = ensure_safe_target_repository_worktree_path(
            target_repository,
            target_repository_issue_worktree_path(target_repository, issue_number),
        )
        checkout_path = target_repository_checkout_path(target_repository)
    except ValueError as exc:
        return 1, str(exc), Path(".")
    checkout_block_reason = verify_target_repository_checkout(target_repository)
    if checkout_block_reason is not None:
        return 1, checkout_block_reason, path
    return prepare_git_issue_worktree(issue_number, checkout_path, path)


def prepare_git_issue_worktree(
    issue_number: int, coordinator_workdir: str | Path, path: Path
) -> tuple[int, str, Path]:
    branch = issue_branch(issue_number)
    outputs: list[str] = []

    if path.exists():
        checks = (
            (["git", "status", "--short"], "dirty"),
            (["git", "branch", "--show-current"], "branch"),
        )
        for command, check_name in checks:
            code, output = run_command(command, cwd=path)
            outputs.append(format_command_output(command, output))
            if code != 0:
                return (
                    code,
                    "Existing issue worktree needs cleanup before reuse.\n\n"
                    + "\n".join(outputs),
                    path,
                )
            if check_name == "dirty" and output.strip():
                return (
                    1,
                    "Existing issue worktree is dirty; cleanup is required before reuse.\n\n"
                    + "\n".join(outputs),
                    path,
                )
            if check_name == "branch" and output.strip() != branch:
                return (
                    1,
                    "Existing issue worktree is on the wrong branch; cleanup is "
                    f"required before reuse. Expected {branch!r}.\n\n"
                    + "\n".join(outputs),
                    path,
                )
        return 0, "\n".join(outputs), path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return 1, f"Unable to create worktree root {path.parent}:\n{exc}", path

    remote_code, remote_output = run_command(
        ["git", "remote", "get-url", "origin"], cwd=coordinator_workdir
    )
    if remote_code != 0:
        return 1, "Unable to read coordinator origin URL.", path
    origin_url = remote_output.strip()
    if not origin_url:
        return 1, "Coordinator origin URL is empty.", path

    commands = (
        (["git", "fetch", "origin"], coordinator_workdir),
        (
            [
                "git",
                "clone",
                "--local",
                "--no-hardlinks",
                "--no-checkout",
                str(Path(coordinator_workdir).resolve()),
                str(path),
            ],
            coordinator_workdir,
        ),
        (["git", "fetch", "origin"], path),
        (["git", "checkout", "-B", branch, "origin/main"], path),
    )
    for command, cwd in commands:
        code, output = run_command(command, cwd=cwd)
        outputs.append(format_command_output(command, output))
        if code != 0:
            return code, "\n".join(outputs), path
        if command[1] == "clone":
            config_code, config_output = run_command(
                ["git", "remote", "set-url", "origin", origin_url], cwd=path
            )
            outputs.append(
                "$ git remote set-url origin <coordinator-origin>\n" + config_output
            )
            if config_code != 0:
                return config_code, "\n".join(outputs), path
    return 0, "\n".join(outputs), path


def prepare_issue_branch(
    issue_number: int, coordinator_workdir: str | Path
) -> tuple[int, str, Path]:
    return prepare_issue_worktree(issue_number, coordinator_workdir)


def cleanup_git_issue_worktree(path: Path, coordinator_workdir: str | Path) -> tuple[int, str]:
    if not path.exists():
        return 0, ""
    cleanup_runtime_artifacts(path)
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return 1, f"Unable to remove issue workspace {path}:\n{exc}"
    return 0, f"$ rm -rf {path}\nremoved"


def cleanup_issue_worktree(
    issue_number: int, coordinator_workdir: str | Path
) -> tuple[int, str]:
    try:
        path = ensure_safe_worktree_path(issue_worktree_path(issue_number))
    except ValueError as exc:
        return 1, str(exc)
    return cleanup_git_issue_worktree(path, coordinator_workdir)


def cleanup_target_repository_issue_worktree(
    target_repository: str, issue_number: int
) -> tuple[int, str]:
    try:
        path = ensure_safe_target_repository_worktree_path(
            target_repository,
            target_repository_issue_worktree_path(target_repository, issue_number),
        )
    except ValueError as exc:
        return 1, str(exc)
    return cleanup_git_issue_worktree(path, target_repository_checkout_path(target_repository))


def issue_workspace_review_note(path: str | Path) -> str:
    return f"\n\nIssue workspace kept for review:\n`{path}`"


def report_runner_lane(report: str, task: RunnerTask | None) -> str:
    if (
        task is None
        or not task.has_lane_metadata
        and not task.has_target_project_metadata
        and not task.has_target_repository_metadata
    ):
        return report
    heading, *details = report.splitlines()
    metadata: list[str] = []
    if task.has_lane_metadata:
        metadata.append(f"Runner Lane: {task.lane.name}")
    if task.has_target_project_metadata:
        metadata.append(f"Target Project: {task.target_project}")
    if task.has_target_repository_metadata:
        metadata.append(f"Target Repository: {task.target_repository}")
    return "\n".join((heading, *metadata, *details))


def final_codex_answer(output: str) -> str:
    """Return the final Codex answer without echoed prompt/transcript text."""
    text = _ANSI_ESCAPE_RE.sub("", output or "")

    transcript_tail = _CODEX_TRANSCRIPT_TAIL_RE.search(text)
    workdir_boundary = text.find("\n--------\nworkdir:")
    prefix_cuts: list[int] = []
    if transcript_tail is not None:
        prefix_cuts.append(transcript_tail.start())
    if workdir_boundary != -1:
        prefix_cuts.append(workdir_boundary)
    if prefix_cuts:
        prefix = text[: min(prefix_cuts)].strip()
        if prefix:
            return prefix

    lines = text.splitlines(keepends=True)
    in_fence = False
    final_status_index: int | None = None
    offset = 0
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and (
            _FINAL_STATUS_DELIVERY_LINE_RE.match(line)
            or _FINAL_RESULT_LINE_RE.match(line)
        ):
            final_status_index = offset
        offset += len(line)
    if final_status_index is not None:
        text = text[final_status_index:]

    transcript_tail = _CODEX_TRANSCRIPT_TAIL_RE.search(text)
    cut = transcript_tail.start() if transcript_tail else len(text)
    workdir_boundary = text.find("\n--------\nworkdir:")
    if workdir_boundary != -1:
        cut = min(cut, workdir_boundary)
    return text[:cut].strip()


def _without_fenced_blocks(text: str) -> str:
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def _first_final_status(output: str) -> str | None:
    result_status = _final_result_status(output)
    if result_status is not None:
        return result_status

    text = _ANSI_ESCAPE_RE.sub("", output or "").lstrip()
    first_line = text.splitlines()[0] if text else ""
    match = _FINAL_STATUS_LINE_RE.match(first_line)
    if match is not None:
        return match.group(1).upper()

    final_answer = _without_fenced_blocks(final_codex_answer(output))
    for line in final_answer.splitlines():
        match = _FINAL_STATUS_DELIVERY_LINE_RE.match(line)
        if match is not None:
            return match.group(1).upper()
    return None


def _final_result_status(output: str) -> str | None:
    final_answer = _without_fenced_blocks(final_codex_answer(output))
    lines = [line for line in final_answer.splitlines() if line.strip()]
    if not lines:
        return None
    for line in (lines[0], lines[-1]):
        match = _FINAL_RESULT_LINE_RE.match(line)
        if match is None:
            continue
        result = match.group("result").upper()
        return result
    return None


def blocked_output_marker(output: str) -> str | None:
    final_answer = _without_fenced_blocks(final_codex_answer(output))
    for marker, marker_re in zip(_BLOCKED_OUTPUT_MARKERS, _BLOCKED_OUTPUT_MARKER_RES):
        if marker_re.search(final_answer):
            return marker
    return None


def classify_codex_task_result(output: str, exit_code: int) -> CodexTaskResult:
    if exit_code != 0:
        return CodexTaskResult("BLOCKED", f"exit code {exit_code}")

    status = _first_final_status(output)
    if status is not None:
        if status == "DONE":
            return CodexTaskResult("DONE")
        return CodexTaskResult("BLOCKED", blocked_output_marker(output) or status)

    marker = blocked_output_marker(output)
    if marker is not None:
        return CodexTaskResult("BLOCKED", marker)
    return CodexTaskResult("DONE")


def runner_report_status(report: str) -> str:
    status = _first_final_status(report)
    if status != "DONE":
        return "BLOCKED"
    report_without_fences = _without_fenced_blocks(report)
    if blocked_output_marker(report_without_fences) is not None:
        return "BLOCKED"
    if _REPORT_OPERATOR_REQUIRED_RE.search(report_without_fences) is not None:
        return "BLOCKED"
    if report.startswith(_LOCAL_WORKTREE_DONE_PREFIX) and not _has_local_worktree_success_evidence(
        report
    ):
        return "BLOCKED"
    if (
        "Codex completed successfully and produced file changes." in report
        and not extract_pr_url(report)
    ):
        return "BLOCKED"
    return "DONE"


def _has_local_worktree_success_evidence(report: str) -> bool:
    required_fragments = (
        _LOCAL_WORKTREE_DONE_PREFIX,
        _LOCAL_WORKTREE_BOUNDED_FINALIZATION_EVIDENCE,
        "Selected Project: ",
        "Selected Repository: ",
        "Issue worktree: `",
        "Target-repo output: not created.",
        "Local worktree changed files:",
        "Local worktree git diff:",
        "Codex output:\n```",
    )
    return all(fragment in report for fragment in required_fragments)


def blocked_final_report(report: str) -> str:
    report = sanitize_public_report(report)
    marker = blocked_output_marker(report)
    if marker is not None:
        reason = f"blocked marker `{marker}` was present"
    elif (
        "Codex completed successfully and produced file changes." in report
        and not extract_pr_url(report)
    ):
        reason = "draft PR URL was missing from a file-change report"
    else:
        reason = "runner report did not meet completion criteria"
    return (
        "BLOCKED: Runner did not mark this task complete.\n\n"
        f"Reason: {reason}.\n\n"
        f"Runner report:\n```\n{report.strip()}\n```"
    )


def blocked_codex_output_report(
    codex_output: str,
    marker: str,
    issue_workdir: str,
) -> str:
    return (
        "BLOCKED: Codex output reported a blocked deliverable.\n\n"
        f"Blocked marker: {marker}\n\n"
        f"Codex output:\n```\n{codex_output.strip()}\n```"
        + issue_workspace_review_note(issue_workdir)
    )


def get_ready_issues() -> list[dict[str, Any]]:
    code, output = run_command(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            REPO,
            "--label",
            LABEL_READY,
            "--state",
            "open",
            "--search",
            "is:issue",
            "--json",
            "number,title,body,state,url,closed",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue list failed:\n{output}")
    parsed = json.loads(output or "[]")
    if not isinstance(parsed, list):
        raise RuntimeError("gh issue list returned non-list JSON")
    return [issue for issue in parsed if is_open_task_issue(issue)]


def is_pull_request_item(item: dict[str, Any]) -> bool:
    if item.get("pull_request") is not None:
        return True
    url = str(item.get("url") or item.get("html_url") or "")
    return "/pull/" in url or "/pulls/" in url


def is_open_task_issue(item: dict[str, Any]) -> bool:
    if is_pull_request_item(item):
        return False
    if item.get("closed") is True:
        return False
    state = item.get("state")
    return state is None or str(state).lower() == "open"


def label_names(labels: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            names.add(label["name"])
    return names


def task_fence_block_reason(body: str) -> str | None:
    lines = (body or "").splitlines()
    index = 0
    while index < len(lines):
        if _TASK_FENCE_OPEN_LINE_RE.match(lines[index]) is None:
            index += 1
            continue
        closing_index: int | None = None
        for candidate_index, candidate in enumerate(lines[index + 1 :], start=index + 1):
            if _FENCE_CLOSE_LINE_RE.match(candidate):
                closing_index = candidate_index
                break
        if closing_index is not None:
            index = closing_index + 1
            continue
        return (
            "missing_closing_task_fence: task fence starts with ```task but "
            "does not include a closing ``` fence."
        )
    return None


def extract_task_block(body: str) -> str | None:
    lines = (body or "").splitlines()
    for index, line in enumerate(lines):
        if _TASK_FENCE_OPEN_LINE_RE.match(line) is None:
            continue
        task_lines: list[str] = []
        for candidate in lines[index + 1 :]:
            if _FENCE_CLOSE_LINE_RE.match(candidate):
                return "\n".join(task_lines).strip()
            task_lines.append(candidate)
        return None
    return None


def extract_runner_lane(body: str) -> tuple[RunnerLane | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    lane_name = _body_field(metadata, "Runner Lane") or _body_field(metadata, "Lane")
    if lane_name is None:
        return DEFAULT_RUNNER_LANE, None
    if lane_name not in ALLOWED_RUNNER_LANES:
        allowed = ", ".join(f"`{name}`" for name in sorted(ALLOWED_RUNNER_LANES))
        return None, f"Runner lane `{lane_name}` is not allowlisted. Use {allowed}."
    return RunnerLane(lane_name), None


def _project_id_for_repo(project_tree: dict[str, Any], repo: str) -> str:
    for project_id, project in project_tree["projects"].items():
        if project["repo"] == repo:
            return project_id
    raise KeyError(f"unknown repo {repo!r}.")


def _target_repository_metadata_field(metadata: str) -> tuple[str | None, str | None]:
    for field in TARGET_REPOSITORY_METADATA_FIELDS:
        value = _body_field(metadata, field)
        if value is not None:
            return value, field
    return None, None


def resolve_target_project_metadata(
    body: str,
) -> tuple[str | None, str | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    target_project = _body_field(metadata, "Target Project")
    target_repository, _target_repository_field = _target_repository_metadata_field(
        metadata
    )
    project_tree = load_runner_project_tree()

    if target_project is None and target_repository is None:
        return "skeleton", QUEUE_REPOSITORY, None

    project_from_project: dict[str, Any] | None = None
    project_from_repository: dict[str, Any] | None = None
    project_id_from_repository: str | None = None

    if target_project is not None:
        try:
            project_from_project = get_project(project_tree, target_project)
        except (KeyError, ValueError) as exc:
            allowed = ", ".join(
                f"`{project}`" for project in sorted(ALLOWED_TARGET_PROJECTS)
            )
            return (
                None,
                None,
                f"Target project `{target_project}` is not allowlisted. Use {allowed}.",
            )
        if project_from_project["public"] is not True:
            return None, None, f"Target project `{target_project}` is not public."

    if target_repository is not None:
        try:
            project_from_repository = get_project_by_repo(project_tree, target_repository)
            project_id_from_repository = _project_id_for_repo(
                project_tree, target_repository
            )
        except KeyError:
            allowed = ", ".join(
                f"`{repo}`" for repo in sorted(ALLOWED_TARGET_REPOSITORIES)
            )
            return (
                None,
                None,
                f"Target repository `{target_repository}` is not allowlisted. Use {allowed}.",
            )
        if project_from_repository["public"] is not True:
            return None, None, f"Target repository `{target_repository}` is not public."

    if target_project is not None and target_repository is not None:
        if project_from_project != project_from_repository:
            return (
                None,
                None,
                "Target Project and Target Repository resolve to different "
                "PROJECT_TREE entries.",
            )
        return target_project, target_repository, None

    if target_project is not None and project_from_project is not None:
        return target_project, project_from_project["repo"], None

    if project_id_from_repository is not None and target_repository is not None:
        return project_id_from_repository, target_repository, None

    return None, None, "Target project metadata could not be resolved."


def extract_target_repository(body: str) -> tuple[str | None, str | None]:
    _target_project, target_repository, reason = resolve_target_project_metadata(body)
    return target_repository, reason


def extract_runner_task(body: str) -> tuple[RunnerTask | None, str | None]:
    fence_reason = task_fence_block_reason(body)
    if fence_reason is not None:
        return None, fence_reason
    content = extract_task_block(body)
    if content is None:
        return None, None
    metadata = (body or "").split("```task", 1)[0]
    lane, lane_reason = extract_runner_lane(body)
    if lane is None:
        return None, lane_reason
    target_project, target_repository, target_reason = resolve_target_project_metadata(
        body
    )
    if target_project is None or target_repository is None:
        return None, target_reason
    return RunnerTask(
        content=content,
        lane=lane,
        has_lane_metadata=(
            _body_field(metadata, "Runner Lane") is not None
            or _body_field(metadata, "Lane") is not None
        ),
        target_project=target_project,
        has_target_project_metadata=(
            _body_field(metadata, "Target Project") is not None
        ),
        target_repository=target_repository,
        has_target_repository_metadata=(
            _target_repository_metadata_field(metadata)[0] is not None
        ),
    ), None


def project_execution_block_reason(task: RunnerTask) -> str | None:
    project = get_project(load_runner_project_tree(), task.target_project)
    execution_modes = project.get("execution_modes") or {}

    if project.get("runner_enabled") is not True:
        return f"Runner is disabled for target project `{task.target_project}`."
    if execution_modes.get("planning_only") is True:
        return (
            f"Target project `{task.target_project}` is planning-only. "
            "Runner will not execute Codex for this project."
        )
    if task.target_repository == QUEUE_REPOSITORY:
        if execution_modes.get("codex_issue_worktree") is True:
            return None
        return (
            f"Target project `{task.target_project}` does not enable "
            "codex issue worktree execution."
        )
    if execution_modes.get("live_cross_repo") is True:
        return (
            "Live cross-repo execution is blocked in this runner stage and "
            "requires a separate PR."
        )
    if execution_modes.get("codex_issue_worktree") is True:
        return None
    return (
        f"Target project `{task.target_project}` does not enable an executable "
        "runner mode."
    )


def extract_runtime_maintenance_task_id(body: str) -> tuple[bool, str | None]:
    mode_found = re.search(
        rf"^\s*Mode:\s*{RUNTIME_MAINTENANCE_MODE}\s*$",
        body or "",
        re.MULTILINE,
    )
    if mode_found is None:
        return False, None

    task_id = re.search(
        r"^\s*Maintenance Task ID:\s*(?P<task_id>[a-z0-9_]+)\s*$",
        body or "",
        re.MULTILINE,
    )
    return True, task_id.group("task_id") if task_id else None


def extract_telegram_approved_pr_merge_request(
    body: str,
) -> tuple[bool, TelegramApprovedPrMergeRequest | None, str | None]:
    mode_found = re.search(
        rf"^\s*Mode:\s*{TELEGRAM_APPROVED_PR_MERGE_MODE}\s*$",
        body or "",
        re.MULTILINE,
    )
    if mode_found is None:
        return False, None, None

    repo = _body_field(body, "Repository")
    pr_number = _body_field(body, "Pull Request")
    head_sha = _body_field(body, "Approved Head SHA")
    action = _body_field(body, "Merge Action")
    approval_source = _body_field(body, "Approval Source")
    callback_digest = _body_field(body, "Callback Digest")
    if repo != REPO:
        return True, None, f"Telegram approved merge repository must be `{REPO}`."
    if not isinstance(pr_number, str) or not re.fullmatch(r"[1-9]\d*", pr_number):
        return True, None, "Telegram approved merge pull request is malformed."
    if not isinstance(head_sha, str) or _HEAD_SHA_RE.fullmatch(head_sha) is None:
        return True, None, "Telegram approved merge head SHA is malformed."
    if action != TELEGRAM_APPROVED_PR_MERGE_ACTION:
        return True, None, "Telegram approved merge action must be squash."
    if approval_source != "signed_telegram_callback":
        return True, None, "Telegram approved merge source is not allowlisted."
    if (
        not isinstance(callback_digest, str)
        or _CALLBACK_DIGEST_RE.fullmatch(callback_digest) is None
    ):
        return True, None, "Telegram approved merge callback digest is malformed."
    return (
        True,
        TelegramApprovedPrMergeRequest(
            pr_number=int(pr_number),
            approved_head_sha=head_sha.lower(),
            callback_digest=callback_digest,
        ),
        None,
    )


def _body_field(body: str, field: str) -> str | None:
    match = re.search(
        rf"^\s*{re.escape(field)}:\s*(?P<value>\S(?:.*\S)?)\s*$",
        body or "",
        re.MULTILINE,
    )
    return match.group("value") if match else None


def _metadata_before_task(body: str) -> str:
    return (body or "").split("```task", 1)[0]


def _metadata_yaml_documents(metadata: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    lines = (metadata or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.fullmatch(r"\s*```\s*(?:yaml|yml)?\s*", line) is None:
            index += 1
            continue
        yaml_lines: list[str] = []
        index += 1
        while index < len(lines) and _FENCE_CLOSE_LINE_RE.match(lines[index]) is None:
            yaml_lines.append(lines[index])
            index += 1
        if index < len(lines):
            index += 1
        try:
            parsed = yaml.safe_load("\n".join(yaml_lines)) if yaml_lines else None
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict):
            documents.append(parsed)
    return documents


def _metadata_yaml_value(metadata: str, key: str) -> object:
    for document in _metadata_yaml_documents(metadata):
        if key in document:
            return document[key]
    return None


def _metadata_multiline_value(metadata: str, field: str) -> object:
    lines = (metadata or "").splitlines()
    for index, line in enumerate(lines):
        if re.fullmatch(rf"\s*{re.escape(field)}:\s*", line) is None:
            continue
        values: list[str] = []
        for item in lines[index + 1 :]:
            if re.fullmatch(r"\s*```\s*(?:yaml|yml)?\s*", item):
                break
            if re.fullmatch(r"\s*[A-Za-z][A-Za-z0-9 _-]{0,80}:\s*.*", item):
                break
            if not item.strip():
                break
            bullet = re.fullmatch(r"\s*-\s+(?P<value>\S(?:.*\S)?)\s*", item)
            if bullet is not None:
                values.append(bullet.group("value"))
                continue
            values.append(item.strip())
        if values:
            return values
        return ""
    return None


def _body_field_or_yaml_value(body: str, field: str, key: str) -> object:
    metadata = _metadata_before_task(body)
    field_value = _body_field(metadata, field)
    if field_value is None:
        field_value = _body_field(metadata, key)
    if field_value is not None:
        return field_value
    multiline_value = _metadata_multiline_value(metadata, field)
    if multiline_value is None and field != key:
        multiline_value = _metadata_multiline_value(metadata, key)
    if multiline_value is not None:
        return multiline_value
    return _metadata_yaml_value(metadata, key)


def _metadata_has_field(metadata: str, field: str) -> bool:
    return (
        re.search(rf"^\s*{re.escape(field)}\s*:", metadata or "", re.MULTILINE)
        is not None
    )


def _requires_expected_output(metadata: str) -> bool:
    if _metadata_has_field(metadata, "Expected Output") or _metadata_has_field(
        metadata, "expected_output"
    ):
        return True
    if _metadata_yaml_value(metadata, "expected_output") is not None:
        return True
    return any(
        _metadata_has_field(metadata, field)
        for field in (
            "Selected Project",
            "Selected Repository",
            "Target Project",
            "Target Repository",
            "required_changes",
            "allowed_files",
            "risk",
        )
    )


def _body_csv_field(body: str, field: str) -> tuple[str, ...]:
    value = _body_field_or_yaml_value(body, field, field.strip().lower().replace(" ", "_"))
    if value is None:
        return ()
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(item.strip() for item in value if item.strip())
    if not isinstance(value, str):
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _expected_output_value(body: str) -> object:
    return _body_field_or_yaml_value(body, "Expected Output", "expected_output")


def code_task_expected_output_block_reason(body: str) -> str | None:
    metadata = _metadata_before_task(body)
    expected_output = _expected_output_value(body)
    if expected_output is None and not _requires_expected_output(metadata):
        return None
    validation = expected_output_validation(expected_output)
    return validation.reason if not validation.accepted else None


def runner_task_route(
    *,
    maintenance_mode: bool,
    maintenance_task_id: str | None,
    merge_mode: bool,
) -> str:
    if maintenance_mode:
        if maintenance_task_id in PUBLISH_ONLY_MAINTENANCE_TASK_IDS:
            return ROUTE_PUBLISH_ONLY
        return ROUTE_RUNTIME_ONLY
    if merge_mode:
        return ROUTE_RUNTIME_ONLY
    return ROUTE_CODE_GENERATION


def runner_shadow_mode_enabled() -> bool:
    configured = os.environ.get(RUNNER_SHADOW_MODE_ENV)
    if configured is None:
        return False
    return configured.strip().lower() in {"1", "true", "yes", "on"}


def protected_runner_shadow_compatibility_bindings() -> RunnerShadowCompatibilityBindings:
    return RunnerShadowCompatibilityBindings(
        legacy_routes=frozenset(
            {
                ROUTE_CODE_GENERATION,
                ROUTE_RUNTIME_ONLY,
                ROUTE_PUBLISH_ONLY,
            }
        ),
        route_task_kind_by_route={
            ROUTE_CODE_GENERATION: "code_edit",
            ROUTE_PUBLISH_ONLY: "publish",
        },
        maintenance_task_kind_by_id={
            task_id: SHADOW_MAINTENANCE_TASK_KIND_BY_ID[task_id]
            for task_id in sorted(RUNTIME_MAINTENANCE_TASK_IDS)
            if task_id in SHADOW_MAINTENANCE_TASK_KIND_BY_ID
        },
        publish_maintenance_task_ids=frozenset(PUBLISH_ONLY_MAINTENANCE_TASK_IDS),
    )


def _shadow_task_block_mapping(body: str) -> Mapping[str, Any]:
    task_block = extract_task_block(body)
    if task_block is None:
        return {}
    try:
        parsed = yaml.safe_load(task_block)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _shadow_field(
    body: str,
    task_fields: Mapping[str, Any],
    public_field: str,
    typed_key: str,
) -> object:
    if typed_key in task_fields:
        return task_fields[typed_key]
    return _body_field_or_yaml_value(body, public_field, typed_key)


def _shadow_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _shadow_csv_or_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(item.strip() for item in value if item.strip())
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return tuple(item.strip() for item in value if item.strip())
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    return ()


def _shadow_validation_commands(value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        return ()
    commands: list[tuple[str, ...]] = []
    for command in value:
        if not isinstance(command, list) or not all(
            isinstance(part, str) for part in command
        ):
            return ()
        commands.append(tuple(command))
    return tuple(commands)


def normalized_runner_shadow_metadata(
    *,
    issue_number: int,
    issue_body: str,
    route: str,
    maintenance_task_id: str | None,
    runner_task: RunnerTask | None,
    merge_request: TelegramApprovedPrMergeRequest | None,
    trusted_approval_references: tuple[str, ...] = (),
    trusted_protected_approval_references: tuple[str, ...] = (),
) -> dict[str, object]:
    task_fields = _shadow_task_block_mapping(issue_body)
    base_sha = _shadow_field(issue_body, task_fields, "Base SHA", "base_sha")
    allowed_files = _shadow_csv_or_sequence(
        _shadow_field(issue_body, task_fields, "Allowed Files", "allowed_files")
    )
    approval_reference = _shadow_field(
        issue_body,
        task_fields,
        "Approval Reference",
        "approval_reference",
    )
    idempotency_key = _shadow_field(
        issue_body,
        task_fields,
        "Idempotency Key",
        "idempotency_key",
    )
    validation_timeout = _shadow_int(
        _shadow_field(
            issue_body,
            task_fields,
            "Validation Timeout Seconds",
            "validation_timeout_seconds",
        )
    )
    privacy_boundary = _shadow_field(
        issue_body,
        task_fields,
        "Privacy Boundary",
        "privacy_boundary",
    )

    current_head_sha: str | None = None
    pr_number: int | None = None
    publish_mode: str | None = None
    if merge_request is not None:
        current_head_sha = merge_request.approved_head_sha
        pr_number = merge_request.pr_number
        publish_mode = "signed_merge"

    return {
        "issue_number": issue_number,
        "legacy_route": route,
        "maintenance_task_id": maintenance_task_id,
        "repo": _body_field(issue_body, "Repository") or REPO,
        "branch": _body_field(issue_body, "Branch") or f"runner/issue-{issue_number}",
        "base_sha": base_sha,
        "allowed_files": allowed_files,
        "privacy_boundary": privacy_boundary,
        "approval_reference": approval_reference,
        "trusted_approval_references": trusted_approval_references,
        "trusted_protected_approval_references": trusted_protected_approval_references,
        "idempotency_key": idempotency_key,
        "validation_timeout_seconds": validation_timeout,
        "requested_capabilities": _shadow_csv_or_sequence(
            _shadow_field(
                issue_body,
                task_fields,
                "Requested Capabilities",
                "requested_capabilities",
            )
        ),
        "forbidden_actions": _shadow_csv_or_sequence(
            _shadow_field(issue_body, task_fields, "Forbidden Actions", "forbidden_actions")
        ),
        "validation_commands": _shadow_validation_commands(
            _shadow_field(
                issue_body,
                task_fields,
                "Validation Commands",
                "validation_commands",
            )
        ),
        "expected_output": _shadow_csv_or_sequence(
            _shadow_field(issue_body, task_fields, "Expected Output", "expected_output")
        ),
        "runner_lane": runner_task.lane.name if runner_task is not None else None,
        "publish_mode": publish_mode,
        "pr_number": pr_number,
        "current_head_sha": current_head_sha,
    }


def evaluate_runner_shadow_hook(
    *,
    issue_number: int,
    issue_body: str,
    route: str,
    maintenance_task_id: str | None,
    runner_task: RunnerTask | None,
    merge_request: TelegramApprovedPrMergeRequest | None,
    trusted_approval_references: tuple[str, ...] = (),
    trusted_protected_approval_references: tuple[str, ...] = (),
) -> RunnerShadowReceipt | None:
    global LAST_RUNNER_SHADOW_RECEIPT
    if not runner_shadow_mode_enabled():
        LAST_RUNNER_SHADOW_RECEIPT = {
            "schema": "skeleton.runner_shadow_receipt.v1",
            "shadow_status": "not_applicable",
            "semantic_route": None,
            "reason_codes": ["SHADOW_MODE_DISABLED"],
            "task_envelope_hash": None,
        }
        return None
    try:
        receipt = evaluate_shadow_from_normalized_metadata(
            normalized_runner_shadow_metadata(
                issue_number=issue_number,
                issue_body=issue_body,
                route=route,
                maintenance_task_id=maintenance_task_id,
                runner_task=runner_task,
                merge_request=merge_request,
                trusted_approval_references=trusted_approval_references,
                trusted_protected_approval_references=trusted_protected_approval_references,
            ),
            protected_runner_shadow_compatibility_bindings(),
        )
    except Exception:
        receipt = blocked_shadow_receipt("SHADOW_EVALUATOR_EXCEPTION")
    LAST_RUNNER_SHADOW_RECEIPT = receipt.to_public_mapping()
    return receipt


def retry_condition_for_issue(
    body: str,
    route: str,
    maintenance_task_id: str | None,
    blocker_reason: str | None = None,
) -> RetryCondition:
    return RetryCondition(
        route=route,
        maintenance_task_id=maintenance_task_id,
        allowed_files=_body_csv_field(body, "Allowed Files"),
        expected_output=json.dumps(_expected_output_value(body), sort_keys=True),
        dependency_state=_body_field(body, "Dependency State"),
        blocker_reason=blocker_reason,
    )


def has_runner_task_body(body: str) -> bool:
    maintenance_mode, _task_id = extract_runtime_maintenance_task_id(body)
    merge_mode, _request, _reason = extract_telegram_approved_pr_merge_request(body)
    return extract_task_block(body) is not None or maintenance_mode or merge_mode


def build_codex_task_prompt(
    task_content: str, workdir: str, task: RunnerTask | None = None
) -> str:
    selected_project_context = ""
    if task is not None:
        selected_project_context = (
            f"Selected Project: {task.target_project}\n"
            f"Selected Repository: {task.target_repository}\n\n"
        )
    return (
        "Runner assigned this task to the issue worktree at:\n"
        f"{workdir}\n\n"
        "Edit files only inside that issue worktree. Do not create or use a separate "
        "clone, checkout, or worktree for task output.\n\n"
        f"{selected_project_context}"
        f"{task_content}"
    )


def selected_codex_model() -> str | None:
    model = os.environ.get(CODEX_MODEL_ENV)
    if model is None:
        return None
    model = model.strip()
    if not model:
        return None
    if _SAFE_CODEX_MODEL_RE.fullmatch(model) is None:
        raise ValueError("Configured Codex model contains unsupported characters.")
    return model


def codex_exec_command(
    task_content: str, workdir: str, task: RunnerTask | None = None
) -> list[str]:
    command = [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
    ]
    model = selected_codex_model()
    if model is not None:
        command.extend(["--model", model])
    command.extend(
        [
            "--cd",
            workdir,
            build_codex_task_prompt(task_content, workdir, task),
        ]
    )
    return command


def run_codex_task(
    task_content: str, workdir: str, task: RunnerTask | None = None
) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="runnerjob-", delete=True
    ) as task_file:
        task_file.write(task_content)
        task_file.flush()
        token = _RUN_COMMAND_ENV_OVERRIDE.set(
            sanitize_codegen_child_environment(os.environ)
        )
        try:
            return run_command(
                codex_exec_command(task_content, workdir, task),
                cwd=workdir,
            )
        finally:
            _RUN_COMMAND_ENV_OVERRIDE.reset(token)


def post_issue_comment(issue_number: int, body: str) -> None:
    body = sanitize_public_report(body)
    code, output = run_command(
        [
            "gh",
            "issue",
            "comment",
            str(issue_number),
            "--repo",
            REPO,
            "--body",
            truncate_comment(body),
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue comment failed:\n{output}")


def get_issue_comments(issue: dict[str, Any]) -> list[dict[str, Any]] | None:
    comments = issue.get("comments")
    if isinstance(comments, list):
        return [comment for comment in comments if isinstance(comment, dict)]
    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        if isinstance(comments, int) and comments > 0:
            return None
        return []
    issue_number = int(issue["number"])
    code, output = run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            REPO,
            "--json",
            "comments",
        ]
    )
    if code != 0:
        return None
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError:
        return None
    parsed_comments = parsed.get("comments") if isinstance(parsed, dict) else None
    if not isinstance(parsed_comments, list):
        return None
    return [comment for comment in parsed_comments if isinstance(comment, dict)]


def repeated_blocker_report(decision: RetryDecision) -> str:
    next_action = decision.next_required_action or "DIAGNOSE"
    return (
        "NEEDS_OPERATOR: Runner retry policy blocked repeated execution.\n\n"
        "status=NEEDS_OPERATOR\n"
        f"route={decision.route}\n"
        f"retry_decision={decision.retry_decision}\n"
        f"retry_attempt={decision.retry_attempt}\n"
        f"blocker_signature={decision.blocker_signature}\n"
        f"changed_condition={str(decision.changed_condition).lower()}\n"
        f"override_used={str(decision.override_used).lower()}\n"
        f"next_required_action={next_action}\n"
        "reason=repeated_blocker"
    )


def unverifiable_retry_history_report(decision: RetryDecision) -> str:
    return (
        "NEEDS_OPERATOR: Runner retry policy could not verify prior Runner history.\n\n"
        "status=NEEDS_OPERATOR\n"
        f"route={decision.route}\n"
        f"retry_decision={NEEDS_OPERATOR}\n"
        f"retry_attempt={decision.retry_attempt}\n"
        f"blocker_signature={decision.blocker_signature}\n"
        f"changed_condition={str(decision.changed_condition).lower()}\n"
        f"override_used={str(decision.override_used).lower()}\n"
        "next_required_action=DIAGNOSE\n"
        "reason=prior_runner_history_unverifiable"
    )


def set_issue_label(issue_number: int, remove: str, add: str) -> None:
    code, output = run_command(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            REPO,
            "--remove-label",
            remove,
            "--add-label",
            add,
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue edit failed:\n{output}")


def apply_runner_lane_label(issue_number: int, task: RunnerTask | None) -> None:
    if task is None or not task.has_lane_metadata:
        return

    label = ensure_runner_lane_label(task.lane)
    code, output = run_command(
        [
            "gh",
            "issue",
            "edit",
            str(issue_number),
            "--repo",
            REPO,
            "--add-label",
            label,
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue lane label edit failed:\n{output}")


def ensure_runner_lane_label(lane: RunnerLane) -> str:
    label = RUNNER_LANE_LABELS.get(lane.name)
    if label is None:
        raise ValueError(f"Refusing to create non-allowlisted Runner lane `{lane.name}`.")

    code, output = run_command(
        [
            "gh",
            "label",
            "list",
            "--repo",
            REPO,
            "--search",
            label,
            "--json",
            "name",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh runner lane label list failed:\n{output}")

    try:
        existing_labels = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh runner lane label list returned invalid JSON:\n{output}") from exc

    if any(
        isinstance(existing_label, dict) and existing_label.get("name") == label
        for existing_label in existing_labels
    ):
        return label

    code, output = run_command(
        [
            "gh",
            "label",
            "create",
            label,
            "--repo",
            REPO,
            "--description",
            RUNNER_LANE_LABEL_DESCRIPTIONS[lane.name],
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh runner lane label create failed:\n{output}")
    return label


def extract_pr_url(report: str) -> str | None:
    match = re.search(r"^Draft PR:\s*(?P<url>\S+)\s*$", report, re.MULTILINE)
    if not match:
        return None
    pr_url = match.group("url")
    if pr_url == "{PR_URL}":
        return None
    return pr_url


def sanitize_public_report(report: str) -> str:
    return re.sub(
        r"(?m)^(?P<label>(?:Draft )?PR):\s*\{PR_URL\}\s*$",
        r"\g<label>: none",
        report,
    )


def runner_memory_config_from_env() -> RunnerMemoryConfig | None:
    db_path = os.environ.get(RUNNER_MEMORY_DB_ENV)
    ledger_path = os.environ.get(RUNNER_MEMORY_LEDGER_ENV)
    if db_path and ledger_path:
        return _pytest_safe_runner_memory_config(
            RunnerMemoryConfig(Path(db_path).expanduser(), Path(ledger_path).expanduser())
        )

    memory_dir = os.environ.get(RUNNER_MEMORY_DIR_ENV)
    if not memory_dir:
        return None

    base = Path(memory_dir).expanduser()
    month = datetime.now(timezone.utc).strftime("%Y_%m")
    return _pytest_safe_runner_memory_config(
        RunnerMemoryConfig(base / "skeleton.db", base / f"events_{month}.jsonl")
    )


def _pytest_safe_runner_memory_config(
    config: RunnerMemoryConfig,
) -> RunnerMemoryConfig | None:
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return config

    tmp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    for path in (config.db_path, config.ledger_path):
        try:
            path.expanduser().resolve(strict=False).relative_to(tmp_root)
        except ValueError:
            return None
    return config


def _public_github_pr_url(report: str) -> str | None:
    pr_url = extract_pr_url(report)
    if pr_url and _PUBLIC_GITHUB_PR_URL_RE.fullmatch(pr_url):
        return pr_url
    return None


def _safe_changed_file(file_name: str) -> str | None:
    normalized = file_name.strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    if (
        normalized.startswith(("/", "~"))
        or "\\" in normalized
        or ".." in Path(normalized).parts
        or lowered.endswith(".env")
        or lowered == ".env"
        or "://" in normalized
        or _SAFE_CHANGED_FILE_RE.fullmatch(normalized) is None
    ):
        return None
    try:
        validate_public_safe_payload({"changed_file": normalized})
    except ValueError:
        return None
    return normalized


def extract_runner_memory_changed_files(report: str) -> list[str]:
    patterns = (
        r"^Changed files:\s*\n(?P<files>(?:- [^\n]+\n?)+)",
        r"^Local worktree changed files:\s*\n(?P<files>(?:- [^\n]+\n?)+)",
    )
    files: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, report or "", re.MULTILINE)
        if match is None:
            continue
        for line in match.group("files").splitlines():
            if not line.startswith("- "):
                continue
            safe_file = _safe_changed_file(line.removeprefix("- "))
            if safe_file is not None and safe_file not in files:
                files.append(safe_file)
    return files


def extract_runner_memory_test_summary(report: str) -> str | None:
    match = re.search(
        r"^Pytest output:\s*\n```(?P<summary>.*?)```",
        report or "",
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return None

    summary_lines = []
    for line in match.group("summary").splitlines():
        stripped = line.strip()
        if stripped and _PYTEST_SUMMARY_LINE_RE.search(stripped):
            summary_lines.append(stripped)
        if len(summary_lines) >= 3:
            break
    if not summary_lines:
        return None

    summary = "\n".join(summary_lines)
    try:
        validate_public_safe_payload({"test_summary": summary})
    except ValueError:
        return "redacted unsafe test summary"
    return summary


def _runner_memory_payload(
    *,
    event_type: str,
    issue_number: int,
    project_id: str,
    runner_status: str,
    executor: str | None,
    status: str | None = None,
    report: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_type": event_type,
        "issue_number": issue_number,
        "project_id": project_id,
        "runner_status": runner_status,
    }
    if executor in {"codex", "openhands", "maintenance"}:
        payload["executor"] = executor
    if status is not None:
        payload["status"] = status
    if report is not None:
        changed = extract_runner_memory_changed_files(report)
        if changed:
            payload["changed_files"] = changed
        test_summary = extract_runner_memory_test_summary(report)
        if test_summary is not None:
            payload["test_summary"] = test_summary
        pr_url = _public_github_pr_url(report)
        if pr_url is not None:
            payload["pr_url"] = pr_url
    validate_public_safe_payload(payload)
    return payload


def _write_runner_memory_payload(
    payload: dict[str, Any],
    *,
    executor_result: bool,
) -> str | None:
    config = runner_memory_config_from_env()
    if config is None:
        return None
    try:
        memory = SkeletonMemory(config.db_path)
        memory.init_schema()
        if executor_result:
            memory.log_executor_run(payload)
        else:
            memory.log_operator_event(payload)
        AuditLedger(config.ledger_path).append(payload)
    except Exception:
        return RUNNER_MEMORY_WARNING
    return None


SKELETON_MEMORY_RECENT_BACKFILL_EVENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "backfill-skeleton-memory-recent-pr-461",
        "event_type": "skeleton_memory_milestone",
        "project_id": "skeleton",
        "actor": "runner_maintenance",
        "source": "operator_approved_backfill",
        "summary": "PR #461 merged memory stage 1.",
        "pull_request": 461,
        "milestone": "memory_stage_1_merged",
    },
    {
        "id": "backfill-skeleton-memory-recent-pr-465",
        "event_type": "skeleton_memory_milestone",
        "project_id": "skeleton",
        "actor": "runner_maintenance",
        "source": "operator_approved_backfill",
        "summary": "PR #465 merged memory stage 2.",
        "pull_request": 465,
        "milestone": "memory_stage_2_merged",
    },
    {
        "id": "backfill-skeleton-memory-recent-runtime-checkout",
        "event_type": "skeleton_memory_milestone",
        "project_id": "skeleton",
        "actor": "runner_maintenance",
        "source": "operator_approved_backfill",
        "summary": "Runtime checkout synced to 269a0e79d2e5b9df83db96b7d1fa837bad0a5baa.",
        "commit": "269a0e79d2e5b9df83db96b7d1fa837bad0a5baa",
        "milestone": "runtime_checkout_synced",
    },
    {
        "id": "backfill-skeleton-memory-recent-issue-468-smoke",
        "event_type": "skeleton_memory_milestone",
        "project_id": "skeleton",
        "actor": "runner_maintenance",
        "source": "operator_approved_backfill",
        "summary": "Issue #468 confirmed live memory smoke wrote records.",
        "issue_number": 468,
        "milestone": "live_memory_smoke_confirmed",
    },
    {
        "id": "backfill-skeleton-memory-recent-openhands-executor-candidate",
        "event_type": "skeleton_memory_milestone",
        "project_id": "skeleton",
        "actor": "runner_maintenance",
        "source": "operator_approved_backfill",
        "summary": "OpenHands exists on Hetzner as executor candidate, but does not own memory.",
        "executor": "openhands",
        "milestone": "executor_candidate_not_memory_owner",
    },
    {
        "id": "backfill-skeleton-memory-recent-pr-458-hold",
        "event_type": "skeleton_memory_milestone",
        "project_id": "skeleton",
        "actor": "runner_maintenance",
        "source": "operator_approved_backfill",
        "summary": "PR #458 remains hold / do not merge until safety-fix.",
        "pull_request": 458,
        "milestone": "pr_hold_until_safety_fix",
    },
    {
        "id": "backfill-skeleton-memory-recent-memory-boundary",
        "event_type": "skeleton_memory_milestone",
        "project_id": "skeleton",
        "actor": "runner_maintenance",
        "source": "operator_approved_backfill",
        "summary": (
            "Skeleton memory is operational state, not Jeeves memory, not private "
            "document storage, not canon write path."
        ),
        "milestone": "memory_boundary_recorded",
    },
)

SKELETON_MEMORY_RECENT_BACKFILL_PROJECT_STATE: dict[str, Any] = {
    "id": "backfill-skeleton-memory-recent-project-state",
    "project_id": "skeleton",
    "source": "operator_approved_backfill",
    "memory_status": "live_memory_operational",
    "stage_1_pr": 461,
    "stage_2_pr": 465,
    "runtime_checkout_commit": "269a0e79d2e5b9df83db96b7d1fa837bad0a5baa",
    "live_smoke_issue": 468,
    "executor_candidate": "openhands",
    "executor_candidate_owns_memory": False,
    "held_pr": 458,
    "held_pr_reason": "hold_do_not_merge_until_safety_fix",
    "boundaries": [
        "operational_state",
        "not_jeeves_memory",
        "not_private_document_storage",
        "not_canon_write_path",
    ],
    "backfill_policy": "explicit_bounded_public_safe_operator_approved",
    "automatic_canon_promotion": False,
    "decision_records_status": "skipped_no_public_skeleton_memory_writer",
}


def _memory_event_exists(memory: SkeletonMemory, event_id: str) -> bool:
    row = memory.connection.execute(
        "SELECT 1 FROM memory_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    return row is not None


def _append_backfill_ledger_event(
    ledger: AuditLedger, payload: dict[str, Any], event_type: str
) -> None:
    ledger.append(
        {
            **payload,
            "id": f"{payload['id']}-ledger",
            "event_type": event_type,
        }
    )


def backfill_skeleton_memory_recent() -> str:
    task_id = BACKFILL_SKELETON_MEMORY_RECENT
    config = runner_memory_config_from_env()
    if config is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=runner_memory_config_missing"],
            "not_met",
        )

    memory_events_written = 0
    memory_events_existing = 0
    ledger_events_written = 0
    project_state_written = 0
    project_state_existing = 0
    decision_records_written = 0
    decision_records_skipped = 1

    memory = SkeletonMemory(config.db_path)
    memory.init_schema()
    ledger = AuditLedger(config.ledger_path)

    for event in SKELETON_MEMORY_RECENT_BACKFILL_EVENTS:
        validate_public_safe_payload(event)
        event_id = str(event["id"])
        if _memory_event_exists(memory, event_id):
            memory_events_existing += 1
            continue
        memory.log_operator_event(dict(event))
        _append_backfill_ledger_event(ledger, dict(event), "skeleton_memory_backfill_event")
        memory_events_written += 1
        ledger_events_written += 1

    state = dict(SKELETON_MEMORY_RECENT_BACKFILL_PROJECT_STATE)
    validate_public_safe_payload(state)
    if memory.get_project_state("skeleton") == state:
        project_state_existing = 1
    else:
        memory.update_project_state("skeleton", state)
        _append_backfill_ledger_event(
            ledger, state, "skeleton_memory_backfill_project_state"
        )
        project_state_written = 1
        ledger_events_written += 1

    return _maintenance_report(
        "DONE",
        task_id,
        [
            f"memory_events_written={memory_events_written}",
            f"memory_events_existing={memory_events_existing}",
            f"project_state_written={project_state_written}",
            f"project_state_existing={project_state_existing}",
            f"ledger_events_written={ledger_events_written}",
            f"decision_records_written={decision_records_written}",
            f"decision_records_skipped={decision_records_skipped}",
        ],
        "met",
    )


def record_runner_task_picked_up(
    issue_number: int,
    project_id: str,
    executor: str | None,
) -> str | None:
    try:
        payload = _runner_memory_payload(
            event_type="runner_task_picked_up",
            issue_number=issue_number,
            project_id=project_id,
            runner_status="RUNNING",
            executor=executor,
        )
    except Exception:
        return RUNNER_MEMORY_WARNING
    return _write_runner_memory_payload(payload, executor_result=False)


def record_runner_executor_result(
    issue_number: int,
    project_id: str,
    status: str,
    runner_status: str,
    executor: str | None,
    report: str | None,
) -> str | None:
    try:
        payload = _runner_memory_payload(
            event_type="runner_task_executor_result",
            issue_number=issue_number,
            project_id=project_id,
            status=status,
            runner_status=runner_status,
            executor=executor,
            report=report,
        )
    except Exception:
        return RUNNER_MEMORY_WARNING
    return _write_runner_memory_payload(payload, executor_result=True)


def append_memory_warning(report: str, warning: str | None) -> str:
    if not warning:
        return report
    return f"{report.rstrip()}\n\n{warning}"


def extract_pr_number(pr_url: str) -> int | None:
    match = re.search(r"/pulls?/(?P<number>[1-9]\d*)(?:/|$)", pr_url)
    if not match:
        return None
    return int(match.group("number"))


def extract_runner_report_pr_binding(
    report: str,
) -> tuple[str | None, tuple[str, ...]]:
    commit = re.search(
        r"^Commit:\s*(?P<sha>[0-9a-fA-F]{40})\s*$", report, re.MULTILINE
    )
    files = re.search(
        r"^Changed files:\s*\n(?P<files>(?:- [^\n]+\n?)+)",
        report,
        re.MULTILINE,
    )
    if not commit or not files:
        return None, ()

    changed_files = tuple(
        line.removeprefix("- ").strip()
        for line in files.group("files").splitlines()
        if line.startswith("- ") and line.removeprefix("- ").strip()
    )
    if not changed_files:
        return None, ()
    return commit.group("sha"), changed_files


def build_telegram_message(
    issue_number: int,
    status: str,
    report: str | None = None,
    target_repository: str | None = None,
) -> str:
    if target_repository is None:
        lines = [
            f"Проєкт: {_telegram_project_name(REPO)}",
            f"Задача: #{issue_number}",
            f"Статус: {status}",
        ]
    else:
        lines = [
            f"Проєкт: {_telegram_project_name(target_repository)}",
            f"Репозиторій: {target_repository}",
            f"Задача: #{issue_number}",
            f"Статус: {status}",
        ]
    if report and target_repository is not None:
        pr_url = extract_pr_url(report)
        if pr_url:
            lines.append(f"PR: {pr_url}")
    return "\n".join(lines)


TELEGRAM_CALLBACK_REPO_KEYS = {
    "alanua/Skeleton": "s",
    "alanua/bauclock": "b",
    "alanua/Lavalamp": "l",
    "alanua/LumenFlow": "f",
}
_NOTIFICATION_ISSUE_CACHE: dict[tuple[int, str], dict[str, Any]] = {}


def _telegram_callback_data(button: dict[str, Any]) -> str:
    callback_payload = button.get("callback_payload")
    payload = callback_payload if isinstance(callback_payload, dict) else {}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    action = re.sub(r"[^a-z_]", "", str(button.get("action") or ""))[:12]
    pr_number = payload.get("pr_number") if isinstance(payload.get("pr_number"), int) else 0
    repo = str(payload.get("repo") or REPO)
    head_sha = str(payload.get("head_sha") or "").lower()
    head_marker = head_sha[:8] if re.fullmatch(r"[0-9a-f]{40}", head_sha) else "nosha"
    hmac_secret = os.environ.get(TELEGRAM_CALLBACK_HMAC_ENV)
    repo_key = TELEGRAM_CALLBACK_REPO_KEYS.get(repo)
    if repo != REPO and repo_key is None:
        raise ValueError("Telegram callback repo is not allowlisted.")
    callback_prefix = (
        f"tpr2:{action}:{repo_key}:p{pr_number}:{head_marker}"
        if repo != REPO
        else f"tpr1:{action}:p{pr_number}:{head_marker}"
    )
    digest = (
        hmac.new(
            hmac_secret.encode("utf-8"),
            callback_prefix.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()[:12]
        if hmac_secret
        else hashlib.sha256(encoded).hexdigest()[:12]
    )
    callback_data = f"{callback_prefix}:{digest}"
    if len(callback_data.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT:
        raise ValueError("Telegram callback_data exceeded its bound.")
    return callback_data


def card_payload_to_inline_keyboard(card_payload: dict[str, Any]) -> dict[str, Any]:
    inline_keyboard = []
    for button in card_payload.get("buttons", []):
        if not isinstance(button, dict) or not isinstance(button.get("label"), str):
            continue

        telegram_button = {"text": button["label"]}
        if isinstance(button.get("url"), str):
            telegram_button["url"] = button["url"]
        else:
            telegram_button["callback_data"] = _telegram_callback_data(button)
        inline_keyboard.append([telegram_button])
    return {"inline_keyboard": inline_keyboard}


def _telegram_project_name(target_repository: str) -> str:
    project_name = target_repository.rsplit("/", 1)[-1].strip()
    return project_name or target_repository


def _build_pr_ready_operator_text(
    pr_number: int,
    target_repository: str = REPO,
    *,
    source_issue_number: int | None = None,
    include_approval_instruction: bool = True,
) -> str:
    del pr_number
    status = "очікує схвалення" if include_approval_instruction else "готово до перегляду"
    comment = (
        "Перевір у ChatGPT перед схваленням."
        if include_approval_instruction
        else "Відкрий PR, якщо потрібні деталі."
    )
    if target_repository == REPO:
        lines = [
            f"Проєкт: {_telegram_project_name(target_repository)}",
        ]
        if source_issue_number is not None:
            lines.append(f"Задача: #{source_issue_number}")
        lines.extend((f"Статус: {status}", f"Коментар: {comment}"))
    else:
        lines = [
            f"Проєкт: {_telegram_project_name(target_repository)}",
            f"Репозиторій: {target_repository}",
        ]
        if source_issue_number is not None:
            lines.append(f"Задача: #{source_issue_number}")
        lines.extend((f"Статус: {status}", f"Коментар: {comment}"))
    return "\n".join(lines)


def _localize_pr_ready_card_payload(
    card_payload: dict[str, Any],
    pr_number: int,
    target_repository: str = REPO,
    source_issue_number: int | None = None,
) -> dict[str, Any]:
    buttons = []
    for button in card_payload.get("buttons", []):
        if not isinstance(button, dict):
            continue
        action = str(button.get("action") or "")
        buttons.append(
            {
                **button,
                "label": TELEGRAM_PR_READY_BUTTON_LABELS.get(
                    action, str(button.get("label") or "")
                ),
            }
        )
    return {
        **card_payload,
        "text": _build_pr_ready_operator_text(
            pr_number,
            target_repository,
            source_issue_number=source_issue_number,
            include_approval_instruction=True,
        ),
        "buttons": buttons,
    }


def _build_details_only_card_payload(
    pr_url: str,
    pr_number: int,
    target_repository: str = REPO,
    source_issue_number: int | None = None,
) -> dict[str, Any]:
    callback_base = {"repo": target_repository, "pr_number": pr_number, "pr_url": pr_url}
    return {
        "text": _build_pr_ready_operator_text(
            pr_number,
            target_repository,
            source_issue_number=source_issue_number,
            include_approval_instruction=False,
        ),
        "buttons": [
            {
                "action": "details",
                "label": TELEGRAM_PR_READY_BUTTON_LABELS["details"],
                "callback_payload": {**callback_base, "action": "details"},
            },
            {
                "action": "open_pr",
                "label": TELEGRAM_PR_READY_BUTTON_LABELS["open_pr"],
                "callback_payload": {**callback_base, "action": "open_pr"},
                "url": pr_url,
            },
        ],
    }


def build_done_pr_ready_card_payload(
    report: str,
    target_repository: str = REPO,
    source_issue_number: int | None = None,
) -> dict[str, Any] | None:
    pr_url = extract_pr_url(report)
    if not pr_url:
        return None

    pr_number = extract_pr_number(pr_url)
    if pr_number is None:
        return None

    head_sha, changed_files = extract_runner_report_pr_binding(report)
    if head_sha is None or not changed_files:
        return _build_details_only_card_payload(
            pr_url, pr_number, target_repository, source_issue_number
        )

    if target_repository != REPO:
        return _build_details_only_card_payload(
            pr_url, pr_number, target_repository, source_issue_number
        )

    try:
        # Runner reports the commit pushed immediately before its draft PR URL;
        # that commit is the reviewed head for this DONE notification.
        return _localize_pr_ready_card_payload(
            build_pr_ready_card_payload(
                repo=REPO,
                pr_number=pr_number,
                head_sha=head_sha,
                changed_files=changed_files,
                test_summary=TELEGRAM_CARD_TEST_SUMMARY,
                risk_summary=TELEGRAM_CARD_RISK_SUMMARY,
                pr_url=pr_url,
            ),
            pr_number,
            target_repository,
            source_issue_number,
        )
    except ValueError:
        return _build_details_only_card_payload(
            pr_url, pr_number, target_repository, source_issue_number
        )


def send_telegram_notification(
    message: str, reply_markup: dict[str, Any] | None = None
) -> None:
    bot_token = os.environ.get("SKELETON_TG_BOT")
    chat_id = os.environ.get("SKELETON_TG_CHAT")
    if not bot_token or not chat_id:
        return

    request_fields = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }
    if reply_markup is not None:
        request_fields["reply_markup"] = json.dumps(
            reply_markup,
            sort_keys=True,
            separators=(",", ":"),
        )
    payload = urllib.parse.urlencode(request_fields).encode("utf-8")
    request = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TELEGRAM_TIMEOUT_SECONDS):
        pass


def get_notification_issue(issue_number: int) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            REPO,
            "--json",
            "number,body,state,url,closed,labels",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh issue view failed:\n{output}")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh issue view returned non-object JSON")
    return parsed


def notification_task_issue(issue_number: int, status: str) -> dict[str, Any] | None:
    expected_label = FINAL_LABELS_BY_STATUS.get(status)
    if expected_label is None:
        return None

    issue = get_notification_issue(issue_number)
    if not is_open_task_issue(issue):
        return None
    if not has_runner_task_body(issue.get("body") or ""):
        return None
    if expected_label not in label_names(issue.get("labels")):
        return None
    _NOTIFICATION_ISSUE_CACHE[(issue_number, status)] = issue
    return issue


def should_notify_task_finished(issue_number: int, status: str) -> bool:
    return notification_task_issue(issue_number, status) is not None


def notification_target_repository(issue: dict[str, Any]) -> str:
    try:
        target_repository, reason = extract_target_repository(str(issue.get("body") or ""))
        if reason is None and target_repository in ALLOWED_TARGET_REPOSITORIES:
            return target_repository
    except Exception:
        pass
    return QUEUE_REPOSITORY


def notify_task_finished(
    issue_number: int, status: str, report: str | None = None
) -> None:
    try:
        if not should_notify_task_finished(issue_number, status):
            return
        issue = _NOTIFICATION_ISSUE_CACHE.pop((issue_number, status), None)
        target_repository = (
            notification_target_repository(issue) if issue is not None else REPO
        )
        plain_target_repository = (
            target_repository if target_repository != REPO else None
        )
        plain_message = build_telegram_message(
            issue_number, status, report, plain_target_repository
        )
        if status != "DONE" or not report:
            send_telegram_notification(plain_message)
            return

        try:
            card_payload = build_done_pr_ready_card_payload(
                report,
                target_repository,
                source_issue_number=issue_number,
            )
        except Exception:
            send_telegram_notification(plain_message)
            return

        if card_payload is None:
            send_telegram_notification(plain_message)
            return

        try:
            send_telegram_notification(
                str(card_payload["text"]),
                card_payload_to_inline_keyboard(card_payload),
            )
        except Exception:
            send_telegram_notification(plain_message)
    except Exception:
        return


def ensure_clean_worktree(workdir: str) -> tuple[bool, str]:
    cleanup_runtime_artifacts(workdir)
    code, output = run_command(["git", "status", "--short"], cwd=workdir)
    if code != 0:
        return False, output
    return output.strip() == "", output


def changed_files(workdir: str) -> list[str]:
    cleanup_runtime_artifacts(workdir)
    files: set[str] = set()
    for command in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        code, output = run_command(command, cwd=workdir)
        if code != 0:
            raise RuntimeError(f"{' '.join(command)} failed:\n{output}")
        files.update(line.strip() for line in output.splitlines() if line.strip())
    return sorted(files)


def finalize_success(issue: dict[str, Any], workdir: str, codex_output: str) -> str:
    issue_number = int(issue["number"])
    files = changed_files(workdir)
    if not files:
        cleanup_runtime_artifacts(workdir)
        return (
            "DONE: Codex completed successfully with no file changes.\n\n"
            "Runtime artifacts cleaned after Codex execution.\n\n"
            f"Codex output:\n```\n{codex_output.strip()}\n```"
        )

    checks: list[tuple[str, str]] = []

    for command in (
        ["git", "diff", "--check"],
        ["python3", "-m", "pytest", "-q"],
    ):
        code, output = _run_finalization_validation_command(command, cwd=workdir)
        checks.append((" ".join(command), output))
        if code != 0:
            raise RuntimeError(f"{' '.join(command)} failed:\n{output}")

    cleanup_runtime_artifacts(workdir)
    files = changed_files(workdir)

    for command in (
        ["git", "add", *files],
        ["git", "diff", "--cached", "--check"],
        ["git", "commit", "-m", f"runner: issue #{issue_number} task"],
        [
            "git",
            "push",
            "--force-with-lease",
            "-u",
            "origin",
            issue_branch(issue_number),
        ],
    ):
        code, output = run_command(command, cwd=workdir)
        checks.append((" ".join(command), output))
        if code != 0:
            raise RuntimeError(f"{' '.join(command)} failed:\n{output}")

    cleanup_runtime_artifacts(workdir)

    code, commit_sha = run_command(["git", "rev-parse", "HEAD"], cwd=workdir)
    if code != 0:
        raise RuntimeError(f"git rev-parse HEAD failed:\n{commit_sha}")
    commit_sha = commit_sha.strip()

    pr_title = f"Runner task #{issue_number}: {issue.get('title', '').strip()}"
    pr_body = f"Automated Runner task from issue #{issue_number}."
    pr_command = [
        "gh",
        "pr",
        "create",
        "--repo",
        REPO,
        "--base",
        "main",
        "--head",
        issue_branch(issue_number),
        "--title",
        pr_title,
        "--body",
        pr_body,
        "--draft",
    ]
    pr_code, pr_output = run_command(pr_command, cwd=workdir)
    if pr_code == 0:
        pr_url = pr_output.strip()
    else:
        view_code, view_output = run_command(
            [
                "gh",
                "pr",
                "view",
                issue_branch(issue_number),
                "--repo",
                REPO,
                "--json",
                "url",
                "--jq",
                ".url",
            ],
            cwd=workdir,
        )
        if view_code != 0:
            raise RuntimeError(
                f"gh pr create failed:\n{pr_output}\n\ngh pr view failed:\n{view_output}"
            )
        pr_url = view_output.strip()

    pytest_output = next(
        output for command, output in checks if command == "python3 -m pytest -q"
    )
    return (
        "DONE: Codex completed successfully and produced file changes.\n\n"
        "Changed files:\n"
        + "\n".join(f"- {file_name}" for file_name in files)
        + "\n\n"
        f"Pytest output:\n```\n{pytest_output.strip()}\n```\n\n"
        f"Commit: {commit_sha}\n"
        f"Draft PR: {pr_url}"
    )


def finalize_local_worktree_success(
    workdir: str, codex_output: str, runner_task: RunnerTask
) -> str:
    files = changed_files(workdir)
    cleanup_runtime_artifacts(workdir)
    file_summary = (
        "Local worktree changed files:\n"
        + "\n".join(f"- {file_name}" for file_name in files)
        if files
        else "Local worktree changed files: none"
    )
    diff_summary = local_worktree_recovery_diff(workdir)
    return (
        f"{_LOCAL_WORKTREE_DONE_PREFIX}\n\n"
        f"{_LOCAL_WORKTREE_BOUNDED_FINALIZATION_EVIDENCE}\n"
        f"Selected Project: {runner_task.target_project}\n"
        f"Selected Repository: {runner_task.target_repository}\n"
        f"Issue worktree: `{workdir}`\n"
        "Target-repo output: not created.\n\n"
        f"{file_summary}\n\n"
        f"{diff_summary}\n\n"
        f"Codex output:\n```\n{codex_output.strip()}\n```"
    )


def local_worktree_recovery_diff(workdir: str) -> str:
    code, output = run_command(
        ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--"], cwd=workdir
    )
    if code != 0:
        return "Local worktree git diff: unavailable (git diff failed)."

    diff = output.strip()
    if not diff:
        return "Local worktree git diff: none"

    try:
        validate_public_safe_payload({"recovery_patch": diff})
    except ValueError:
        return (
            "Local worktree git diff: omitted because the patch is not bounded "
            "public-safe issue-comment content."
        )

    return f"Local worktree git diff:\n```diff\n{diff}\n```"


def block_issue(
    issue_number: int,
    message: str,
    remove_label: str = LABEL_READY,
    runner_task: RunnerTask | None = None,
    result_status: str = "BLOCKED",
    retry_decision: RetryDecision | None = None,
) -> None:
    report = report_runner_lane(f"BLOCKED: {message}", runner_task)
    if retry_decision is not None:
        report = append_retry_fields(report, retry_decision)
    warning = record_runner_executor_result(
        issue_number,
        runner_task.target_project if runner_task is not None else "skeleton",
        result_status,
        "BLOCKED",
        "codex" if runner_task is not None else None,
        report,
    )
    post_issue_comment(issue_number, append_memory_warning(report, warning))
    set_issue_label(issue_number, remove_label, LABEL_BLOCKED)
    notify_task_finished(issue_number, "BLOCKED")


def _maintenance_report(
    status: str, task_id: str, status_lines: list[str], success_criteria: str
) -> str:
    if status == "DONE":
        heading = "DONE: Runner host maintenance task completed."
    elif status == "NEEDS_OPERATOR" or task_id == PUBLISH_EXISTING_ISSUE_WORKTREE:
        heading = "NEEDS_OPERATOR: Runner host maintenance task needs operator action."
    else:
        heading = "BLOCKED: Runner host maintenance task did not complete."
    return "\n".join(
        (
            heading,
            f"maintenance_task_id={task_id}",
            *_sanitize_maintenance_status_lines(status_lines),
            f"success_criteria={success_criteria}",
        )
    )


def _loop_state_db_path() -> tuple[Path | None, str | None]:
    return _executor_loop_state_db_path(
        environment=os.environ,
        env_var_name=LOOP_STATE_DB_ENV,
        root=ROOT,
        path_has_symlink_component=_path_has_symlink_component,
        path_is_relative_to=_path_is_relative_to,
    )


def _loop_task_packet_from_body(body: str) -> object:
    return _executor_loop_task_packet_from_body(
        body,
        extract_task_block=extract_task_block,
    )


def _loop_receipt_status_line(key: str, value: object) -> str:
    return _executor_loop_receipt_status_line(key, value)


def _loop_receipt_report(receipt: object) -> str:
    return _executor_loop_receipt_report(
        receipt,
        task_id=LOOP_ENGINE_PACKET,
        maintenance_report=_maintenance_report,
    )


def loop_engine_packet(body: str) -> str:
    return _execute_loop_engine_packet(
        body,
        task_id=LOOP_ENGINE_PACKET,
        state_db_path=_loop_state_db_path,
        task_packet_from_body=_loop_task_packet_from_body,
        receipt_report=_loop_receipt_report,
        maintenance_report=_maintenance_report,
        store_factory=LoopStateStore,
        engine_factory=LoopEngine,
        policy_factory=LoopPolicy,
        packet_runner=run_loop_task_packet,
    )


def _sanitize_maintenance_status_lines(status_lines: list[str]) -> list[str]:
    sanitized: list[str] = []
    for line in status_lines:
        safe_line = _sanitize_maintenance_status_line(line)
        if safe_line is not None:
            sanitized.append(safe_line)
    return sanitized


def _maintenance_status_value_is_safe(key: str, value: str) -> bool:
    if (
        not value
        or "=" in value
        or _MAINTENANCE_SENSITIVE_VALUE_RE.search(value)
        or value.startswith(("/", "~"))
        or "\\" in value
        or ".." in value
    ):
        return False
    if key in _MAINTENANCE_PR_URL_KEYS:
        return _PUBLIC_GITHUB_PR_URL_RE.fullmatch(value) is not None
    if "://" in value:
        return False
    return _MAINTENANCE_SYMBOLIC_VALUE_RE.fullmatch(value) is not None


def _sanitize_maintenance_status_line(line: str) -> str | None:
    if "\n" in line or "\r" in line:
        return None
    tokens = line.strip().split()
    if not tokens:
        return None
    normalized: list[str] = []
    for token in tokens:
        if token.count("=") != 1:
            return None
        key, value = token.split("=", 1)
        if (
            _MAINTENANCE_ASSIGNMENT_KEY_RE.fullmatch(key) is None
            or key not in _MAINTENANCE_PUBLIC_STATUS_KEYS
            or key in _MAINTENANCE_FORBIDDEN_STATUS_KEYS
            or not _maintenance_status_value_is_safe(key, value)
        ):
            return None
        normalized.append(f"{key}={value}")
    return " ".join(normalized)


_HOST_INVENTORY_VALUE_RE = re.compile(r"[^A-Za-z0-9._:+-]+")


def _sanitized_host_inventory_value(value: object, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    sanitized = _HOST_INVENTORY_VALUE_RE.sub("_", text).strip("._:+-")
    return (sanitized or fallback)[:80]


def hermes_worker_preflight() -> str:
    task_id = HERMES_WORKER_PREFLIGHT
    try:
        uname = os.uname()
    except AttributeError:
        system = sys.platform
        kernel_release = "unknown"
        machine = "unknown"
        nodename = ""
    else:
        system = uname.sysname
        kernel_release = uname.release
        machine = uname.machine
        nodename = uname.nodename

    host_id = (
        hashlib.sha256(nodename.encode("utf-8", errors="ignore")).hexdigest()[:12]
        if nodename
        else "unknown"
    )
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    tool_lines = [
        f"tool_{tool}=present" if shutil.which(tool) else f"tool_{tool}=missing"
        for tool in ("python3", "git", "gh", "codex")
    ]
    status_lines = [
        "inventory_schema=hermes_worker_preflight_v1",
        "report_mode=read_only",
        f"host_id_sha256_12={host_id}",
        f"system={_sanitized_host_inventory_value(system)}",
        f"kernel_release={_sanitized_host_inventory_value(kernel_release)}",
        f"machine={_sanitized_host_inventory_value(machine)}",
        f"python_version={_sanitized_host_inventory_value(python_version)}",
        f"runner_root_exists={str(ROOT.exists()).lower()}",
        *tool_lines,
    ]
    return _maintenance_report("DONE", task_id, status_lines, "met")


_PRIVATE_MEMORY_HEALTHCHECK_REPORT_KEYS = (
    "schema",
    "status",
    "db_configured",
    "db_openable",
    "integrity_ok",
    "schema_present",
    "table_count",
    "writable_when_requested",
    "heartbeat_ok",
    "error_class",
    "next_operator_action",
)
_PRIVATE_MEMORY_UNSAFE_VALUE_RE = re.compile(
    r"(?i)(/|\\|file:|\.sqlite\b|\.db\b|secret|token|password|credential|drive|"
    r"private_memory_[a-z_]*heartbeat|select\s|create\s+table)"
)
_HERMES_BRIDGE_SAFE_STATUS_VALUES = frozenset({"DONE", "BLOCKED"})
GRAPHIFY_RUNTIME_APPROVAL = "install_graphify_runtime_v1"
UV_PINNED_VERSION = "0.11.24"
GRAPHIFY_PINNED_VERSION = "0.8.44"
GRAPHIFY_SMOKE_TIMEOUT_SECONDS = 45
GRAPHIFY_RUNTIME_COMMAND_TIMEOUT_SECONDS = 120
GRAPHIFY_PACKAGE_TOOLING_TIMEOUT_SECONDS = 30
GRAPHIFY_USER_BOOTSTRAP_TIMEOUT_SECONDS = 120
GRAPHIFY_UV_PACKAGE_TOOLING_UNAVAILABLE_REASON = "graphify_python_package_tooling_unavailable"
GRAPHIFY_PIP_BREAK_SYSTEM_PACKAGES_FLAG = "--break-system-packages"
GRAPHIFY_TOOL_INSTALL_COMMAND = (
    "uv",
    "tool",
    "install",
    "--reinstall",
    f"graphifyy=={GRAPHIFY_PINNED_VERSION}",
)
GRAPHIFY_UV_TOOL_BIN_DIR_COMMAND = ("uv", "tool", "dir", "--bin")
GRAPHIFY_UV_TOOL_ROOT_DIR_COMMAND = ("uv", "tool", "dir")
GRAPHIFY_UV_USER_BOOTSTRAP_COMMAND = (
    "python",
    "-m",
    "pip",
    "install",
    "--user",
    "--disable-pip-version-check",
    "--no-input",
    f"uv=={UV_PINNED_VERSION}",
)
GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND = (
    "graphify",
    "install",
    "--platform",
    "codex",
)
GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND = (
    "graphify",
    "install",
    "--platform",
    "hermes",
)
GRAPHIFY_VERSION_COMMAND = ("graphify", "--version")
GRAPHIFY_INSTALL_HELP_COMMAND = ("graphify", "install", "--help")
GRAPHIFY_BUILD_HELP_COMMAND = ("graphify", "--help")
GRAPHIFY_PROFILE_ROOT_RELATIVE_PATHS = (
    Path(".codex") / "skills" / "graphify",
    Path(".codex") / "skills" / "graphify.md",
    Path(".hermes") / "skills" / "graphify",
    Path(".hermes") / "skills" / "graphify.md",
)
GRAPHIFY_VERSION_MARKER_FILENAME = ".graphify_version"
GRAPHIFY_UPSTREAM_COMMIT = "5d053721aba875156cf2a6ddd6953d8beee98147"
GRAPHIFY_RUNTIME_UNEXPECTED_FAILURE_REASON = "graphify_runtime_unexpected_failure"
GRAPHIFY_COMMAND_PERMISSION_REASON = "graphify_command_permission_denied"
GRAPHIFY_COMMAND_LAUNCH_REASON = "graphify_command_launch_failed"
GRAPHIFY_COMMAND_TIMEOUT_REASON = "graphify_command_timeout"
GRAPHIFY_UV_VERSION_MISMATCH_REASON = "graphify_uv_version_mismatch"
GRAPHIFY_UPSTREAM_PLATFORM_SKILL_RELATIVE_PATHS = {
    "aider": (Path(".aider") / "graphify" / "SKILL.md",),
    "amp": (Path(".config") / "agents" / "skills" / "graphify" / "SKILL.md",),
    "antigravity": (
        Path(".gemini") / "config" / "skills" / "graphify" / "SKILL.md",
    ),
    "antigravity-windows": (
        Path(".gemini") / "config" / "skills" / "graphify" / "SKILL.md",
    ),
    "claude": (Path(".claude") / "skills" / "graphify" / "SKILL.md",),
    "claw": (Path(".openclaw") / "skills" / "graphify" / "SKILL.md",),
    "codebuddy": (Path(".codebuddy") / "skills" / "graphify" / "SKILL.md",),
    "codex": (Path(".codex") / "skills" / "graphify" / "SKILL.md",),
    "copilot": (Path(".copilot") / "skills" / "graphify" / "SKILL.md",),
    "devin": (Path(".config") / "devin" / "skills" / "graphify" / "SKILL.md",),
    "droid": (Path(".factory") / "skills" / "graphify" / "SKILL.md",),
    "hermes": (Path(".hermes") / "skills" / "graphify" / "SKILL.md",),
    "kilo": (Path(".config") / "kilo" / "skills" / "graphify" / "SKILL.md",),
    "kiro": (Path(".kiro") / "skills" / "graphify" / "SKILL.md",),
    "kimi": (Path(".kimi") / "skills" / "graphify" / "SKILL.md",),
    "opencode": (
        Path(".config") / "opencode" / "skills" / "graphify" / "SKILL.md",
    ),
    "pi": (Path(".pi") / "agent" / "skills" / "graphify" / "SKILL.md",),
    "trae": (Path(".trae") / "skills" / "graphify" / "SKILL.md",),
    "trae-cn": (Path(".trae-cn") / "skills" / "graphify" / "SKILL.md",),
    "windows": (Path(".claude") / "skills" / "graphify" / "SKILL.md",),
}
GRAPHIFY_CLAUDE_CONFIG_PLATFORM_NAMES = frozenset(("claude", "windows"))


@dataclass(frozen=True)
class GraphifyProfileBackup:
    root: Path
    entries: tuple[tuple[Path, Path], ...]
    managed_paths: tuple[Path, ...]


def _private_memory_healthcheck_write_requested(body: str) -> tuple[bool, str | None]:
    payload = extract_task_block(body) or ""
    if not payload.strip():
        return False, None

    requested: bool | None = None
    for line in payload.splitlines():
        if "=" in line:
            raw_key, raw_value = line.split("=", 1)
        elif ":" in line:
            raw_key, raw_value = line.split(":", 1)
        else:
            continue
        key = raw_key.strip().lower().replace("-", "_").replace(" ", "_")
        if key not in {"request_write", "write_mode", "heartbeat_write"}:
            continue
        value = raw_value.strip().lower()
        if value in {"true", "yes", "1"}:
            requested = True
        elif value in {"false", "no", "0"}:
            requested = False
        else:
            return False, "invalid_write_request_value"

    return bool(requested), None


def _private_memory_report_lines(report: dict[str, object]) -> tuple[list[str], str | None]:
    if not isinstance(report, dict):
        return [], "invalid_connector_report"
    unexpected_keys = set(report) - set(_PRIVATE_MEMORY_HEALTHCHECK_REPORT_KEYS)
    if unexpected_keys:
        return [], "unsafe_connector_report_key"

    lines: list[str] = []
    for key in _PRIVATE_MEMORY_HEALTHCHECK_REPORT_KEYS:
        value = report.get(key)
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, int):
            text = str(value)
        elif value is None:
            text = "none"
        elif isinstance(value, str):
            text = value
        else:
            return [], "invalid_connector_report_value"
        if _PRIVATE_MEMORY_UNSAFE_VALUE_RE.search(text):
            return [], "privacy_violation"
        lines.append(f"private_memory_{key}={text}")
    return lines, None


def private_memory_healthcheck(body: str = "") -> str:
    task_id = PRIVATE_MEMORY_HEALTHCHECK
    request_write, reason = _private_memory_healthcheck_write_requested(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")

    if request_write:
        connector_report = write_public_heartbeat(
            "runner-private-memory-healthcheck-v0",
            source="runner-maintenance",
            env=os.environ,
        )
    else:
        connector_report = healthcheck_private_memory(env=os.environ)

    status_lines, reason = _private_memory_report_lines(connector_report)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")

    status_lines.insert(
        0,
        f"private_memory_write_requested={'true' if request_write else 'false'}",
    )
    if connector_report.get("status") == "DONE":
        return _maintenance_report("DONE", task_id, status_lines, "met")
    status_lines.append("reason=private_memory_healthcheck_not_ready")
    return _maintenance_report("BLOCKED", task_id, status_lines, "not_met")


def _hermes_bridge_step_status(report: object) -> str:
    if not isinstance(report, dict):
        return "BLOCKED"
    status = report.get("status")
    if status in _HERMES_BRIDGE_SAFE_STATUS_VALUES:
        return str(status)
    return "BLOCKED"


def _hermes_bridge_exception_report(task_id: str) -> str:
    return _maintenance_report(
        "BLOCKED",
        task_id,
        [
            "hermes_bridge_status=BLOCKED",
            "orient_status=BLOCKED",
            "blocked_write_status=BLOCKED",
            "gated_heartbeat_status=BLOCKED",
            "gated_note_status=BLOCKED",
            "public_safe_report_ok=true",
            "error_class=HermesBridgeException",
            "next_operator_action=safe_operator_review",
        ],
        "not_met",
    )


def hermes_private_memory_bridge_check() -> str:
    task_id = HERMES_PRIVATE_MEMORY_BRIDGE_CHECK
    try:
        orient_report = orient_hermes_private_memory(env=os.environ)
        blocked_write_report = write_hermes_private_memory_heartbeat(
            "synthetic-runner-hermes-bridge-blocked-write-v0",
            env=os.environ,
        )
        gated_heartbeat_report = write_hermes_private_memory_heartbeat(
            "synthetic-runner-hermes-bridge-heartbeat-v0",
            write_enabled=True,
            env=os.environ,
        )
        gated_note_report = record_hermes_private_memory_note(
            "synthetic-runner-hermes-bridge-note-v0",
            "runner bridge check",
            write_enabled=True,
            env=os.environ,
        )
    except Exception:
        return _hermes_bridge_exception_report(task_id)

    orient_status = _hermes_bridge_step_status(orient_report)
    blocked_write_status = _hermes_bridge_step_status(blocked_write_report)
    gated_heartbeat_status = _hermes_bridge_step_status(gated_heartbeat_report)
    gated_note_status = _hermes_bridge_step_status(gated_note_report)
    bridge_status = (
        "DONE"
        if blocked_write_status == "BLOCKED"
        and gated_heartbeat_status == "DONE"
        and gated_note_status == "DONE"
        else "BLOCKED"
    )
    next_operator_action = "none" if bridge_status == "DONE" else "safe_operator_review"
    return _maintenance_report(
        bridge_status,
        task_id,
        [
            f"hermes_bridge_status={bridge_status}",
            f"orient_status={orient_status}",
            f"blocked_write_status={blocked_write_status}",
            f"gated_heartbeat_status={gated_heartbeat_status}",
            f"gated_note_status={gated_note_status}",
            "public_safe_report_ok=true",
            "error_class=none",
            f"next_operator_action={next_operator_action}",
        ],
        "met" if bridge_status == "DONE" else "not_met",
    )


def _graphify_private_snapshot_parent() -> Path:
    configured = os.environ.get("SKELETON_GRAPHIFY_RECOVERY_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / "skeleton" / "graphify-recovery"


def _graphify_existing_version_marker_paths(home: Path | None = None) -> tuple[Path, ...]:
    profile_home = home or Path.home()
    markers: list[Path] = []
    seen: set[Path] = set()
    for skill_relative_paths in GRAPHIFY_UPSTREAM_PLATFORM_SKILL_RELATIVE_PATHS.values():
        for skill_relative_path in skill_relative_paths:
            skill_path = profile_home / skill_relative_path
            marker = skill_path.parent / GRAPHIFY_VERSION_MARKER_FILENAME
            if marker in seen:
                continue
            if skill_path.exists() or skill_path.is_symlink():
                markers.append(marker)
                seen.add(marker)
    claude_config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if claude_config_dir:
        skill_path = (
            Path(claude_config_dir).expanduser()
            / "skills"
            / "graphify"
            / "SKILL.md"
        )
        marker = skill_path.parent / GRAPHIFY_VERSION_MARKER_FILENAME
        if marker not in seen and (skill_path.exists() or skill_path.is_symlink()):
            markers.append(marker)
    return tuple(markers)


def _graphify_managed_profile_paths(home: Path | None = None) -> tuple[Path, ...]:
    profile_home = home or Path.home()
    root_paths = tuple(
        profile_home / relative_path
        for relative_path in GRAPHIFY_PROFILE_ROOT_RELATIVE_PATHS
    )
    return (*root_paths, *_graphify_existing_version_marker_paths(profile_home))


def _graphify_existing_version_marker_count(home: Path | None = None) -> int:
    return len(_graphify_existing_version_marker_paths(home))


def _path_contains_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    if not path.is_dir():
        return False
    try:
        return any(child.is_symlink() for child in path.rglob("*"))
    except OSError:
        return True


def _path_has_existing_symlink_parent(path: Path) -> bool:
    home = Path.home()
    current = path.parent
    while current != current.parent:
        if current.exists() and current.is_symlink():
            return True
        if current == home:
            return False
        current = current.parent
    return False


def _remove_graphify_profile_path(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return True
        if path.is_dir():
            shutil.rmtree(path)
            return True
        return True
    except OSError:
        return False


def _copy_graphify_profile_path(source: Path, destination: Path) -> bool:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copy2(source, destination)
            return True
        if source.is_dir():
            shutil.copytree(source, destination, copy_function=shutil.copy2)
            return True
        return False
    except OSError:
        return False


def _existing_graphify_profile_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    existing = [path for path in paths if path.exists() or path.is_symlink()]
    roots = [
        path
        for path in existing
        if path.name == "graphify.md"
        or (path.name == "graphify" and path.parent.name == "skills")
    ]
    filtered: list[Path] = []
    for path in existing:
        if any(root != path and _path_is_relative_to(path, root) for root in roots):
            continue
        filtered.append(path)
    return tuple(filtered)


def _backup_graphify_profiles() -> tuple[GraphifyProfileBackup | None, str, int]:
    managed_paths = _graphify_managed_profile_paths()
    for path in managed_paths:
        if _path_has_existing_symlink_parent(path) or (
            path.exists() and _path_contains_symlink(path)
        ):
            return None, "unsafe_symlink", 0

    try:
        parent = _graphify_private_snapshot_parent()
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent.chmod(0o700)
        backup_root = Path(
            tempfile.mkdtemp(prefix="snapshot-", dir=str(parent))
        )
        backup_root.chmod(0o700)
    except OSError:
        return None, "backup_root_failed", 0

    entries: list[tuple[Path, Path]] = []
    for index, path in enumerate(_existing_graphify_profile_paths(managed_paths)):
        destination = backup_root / str(index)
        if not _copy_graphify_profile_path(path, destination):
            shutil.rmtree(backup_root, ignore_errors=True)
            return None, "backup_copy_failed", len(entries)
        entries.append((path, destination))
    return GraphifyProfileBackup(backup_root, tuple(entries), managed_paths), "created", len(entries)


def _restore_graphify_profiles(backup: GraphifyProfileBackup) -> str:
    backed_up_paths = {path for path, _backup_path in backup.entries}
    for path in sorted(backup.managed_paths, key=lambda item: len(item.parts), reverse=True):
        if path.exists() or path.is_symlink():
            if not _remove_graphify_profile_path(path):
                return "failed"
    for path, backup_path in backup.entries:
        if path in backed_up_paths and not _copy_graphify_profile_path(backup_path, path):
            return "failed"
    return "restored"


def _restore_graphify_profiles_safely(backup: GraphifyProfileBackup) -> str:
    try:
        return _restore_graphify_profiles(backup)
    except Exception:
        return "failed"


def _graphify_runtime_blocked_report(
    status_lines: list[str],
    reason: str,
    rollback_status: str = "not_needed",
) -> str:
    return _maintenance_report(
        "BLOCKED",
        INSTALL_GRAPHIFY_RUNTIME,
        [*status_lines, f"rollback_status={rollback_status}", f"reason={reason}"],
        "not_met",
    )


def _graphify_python_executable() -> Path | None:
    executable = Path(sys.executable).expanduser()
    if not executable.is_absolute():
        return None
    try:
        resolved = executable.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return resolved


def _graphify_user_scripts_dir() -> Path | None:
    raw_path = sysconfig.get_path("scripts", f"{os.name}_user")
    if not raw_path:
        return None
    scripts_dir = Path(raw_path).expanduser()
    if not scripts_dir.is_absolute():
        return None
    try:
        return scripts_dir.resolve(strict=False)
    except OSError:
        return None


def _graphify_validate_executable(path: Path, expected_parent: Path | None = None) -> Path | None:
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if expected_parent is not None:
        expected_resolved = expected_parent.resolve(strict=False)
        if not _path_is_relative_to(resolved, expected_resolved):
            return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return resolved


def _graphify_resolve_path_executable(command_name: str) -> Path | None:
    resolved = shutil.which(command_name)
    if resolved is None:
        return None
    return _graphify_validate_executable(Path(resolved).expanduser())


def _graphify_resolve_user_script(command_name: str) -> Path | None:
    scripts_dir = _graphify_user_scripts_dir()
    if scripts_dir is None:
        return None
    candidates = [scripts_dir / command_name]
    if os.name == "nt" and not command_name.lower().endswith(".exe"):
        candidates.append(scripts_dir / f"{command_name}.exe")
    for candidate in candidates:
        executable = _graphify_validate_executable(candidate, scripts_dir)
        if executable is not None:
            return executable
    return None


def _graphify_command_with_executable(command: tuple[str, ...], executable: Path) -> list[str]:
    return [str(executable), *command[1:]]


def _run_graphify_python_command(
    command: list[str],
    *,
    timeout: int,
    cwd: str | Path | None = None,
) -> tuple[int | None, str, str | None]:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                **os.environ,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1",
            },
        )
    except subprocess.TimeoutExpired:
        return None, "", GRAPHIFY_COMMAND_TIMEOUT_REASON
    except Exception as error:
        return None, "", _graphify_command_exception_reason(
            error,
            GRAPHIFY_UV_PACKAGE_TOOLING_UNAVAILABLE_REASON,
        )
    return result.returncode, result.stdout + result.stderr, None


def _graphify_python_package_tooling_preflight(status_lines: list[str]) -> str | None:
    python_executable = _graphify_python_executable()
    if python_executable is None:
        status_lines.append("step=verify_python_package_tooling status=failed")
        return GRAPHIFY_UV_PACKAGE_TOOLING_UNAVAILABLE_REASON
    command = [str(python_executable), "-m", "pip", "--version"]
    code, _output, reason = _run_graphify_python_command(
        command,
        timeout=GRAPHIFY_PACKAGE_TOOLING_TIMEOUT_SECONDS,
        cwd=ROOT,
    )
    if reason is not None or code != 0:
        status_lines.append("step=verify_python_package_tooling status=failed")
        return reason or GRAPHIFY_UV_PACKAGE_TOOLING_UNAVAILABLE_REASON
    status_lines.append("step=verify_python_package_tooling status=done")
    return None


def _graphify_pip_failure_is_externally_managed(output: str) -> bool:
    normalized = output.lower()
    return "externally-managed-environment" in normalized or (
        "externally managed" in normalized
        and GRAPHIFY_PIP_BREAK_SYSTEM_PACKAGES_FLAG in normalized
    )


def _graphify_uv_user_bootstrap_command(
    python_executable: Path,
    *,
    use_break_system_packages: bool,
) -> list[str]:
    command = [str(python_executable), *GRAPHIFY_UV_USER_BOOTSTRAP_COMMAND[1:]]
    if not use_break_system_packages:
        return command
    insert_at = len(command) - 1
    return [
        *command[:insert_at],
        GRAPHIFY_PIP_BREAK_SYSTEM_PACKAGES_FLAG,
        *command[insert_at:],
    ]


def _graphify_bootstrap_uv(status_lines: list[str]) -> tuple[Path | None, str | None]:
    reason = _graphify_python_package_tooling_preflight(status_lines)
    if reason is not None:
        return None, reason
    python_executable = _graphify_python_executable()
    if python_executable is None:
        status_lines.append("step=bootstrap_pinned_uv_tool status=failed")
        return None, GRAPHIFY_UV_PACKAGE_TOOLING_UNAVAILABLE_REASON
    code, output, reason = _run_graphify_python_command(
        _graphify_uv_user_bootstrap_command(
            python_executable,
            use_break_system_packages=False,
        ),
        timeout=GRAPHIFY_USER_BOOTSTRAP_TIMEOUT_SECONDS,
        cwd=ROOT,
    )
    if reason is not None or code != 0:
        if reason is not None or not _graphify_pip_failure_is_externally_managed(output):
            status_lines.append("step=bootstrap_pinned_uv_tool status=failed")
            return None, reason or "graphify_uv_bootstrap_failed"
        status_lines.append("step=bootstrap_pinned_uv_tool status=externally_managed")
        code, _fallback_output, reason = _run_graphify_python_command(
            _graphify_uv_user_bootstrap_command(
                python_executable,
                use_break_system_packages=True,
            ),
            timeout=GRAPHIFY_USER_BOOTSTRAP_TIMEOUT_SECONDS,
            cwd=ROOT,
        )
        if reason is not None or code != 0:
            status_lines.append("step=bootstrap_pinned_uv_tool_fallback status=failed")
            return None, reason or "graphify_uv_bootstrap_failed"
        status_lines.append("step=bootstrap_pinned_uv_tool_fallback status=done")
    uv_executable = _graphify_resolve_user_script("uv")
    if uv_executable is None:
        status_lines.append("step=resolve_pinned_uv_tool status=failed")
        return None, "graphify_tool_command_unavailable"
    status_lines.extend(
        (
            "step=bootstrap_pinned_uv_tool status=done",
            "step=resolve_pinned_uv_tool status=done",
        )
    )
    return uv_executable, None


def _graphify_parse_uv_tool_dir_output(output: str) -> Path | None:
    lines = output.splitlines()
    if len(lines) != 1:
        return None
    raw_path = lines[0]
    if not raw_path or raw_path.strip() != raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        return None
    try:
        return path.resolve(strict=False)
    except OSError:
        return None


def _graphify_resolve_uv_tool_dir(
    uv_executable: Path,
    command: tuple[str, ...],
) -> tuple[Path | None, str | None]:
    code, output, reason = _run_graphify_runtime_command(
        _graphify_command_with_executable(command, uv_executable),
        command_unavailable_reason="graphify_tool_command_unavailable",
        cwd=ROOT,
    )
    if reason is not None:
        return None, reason
    if code != 0:
        return None, "graphify_tool_command_unavailable"
    path = _graphify_parse_uv_tool_dir_output(output)
    if path is None:
        return None, "graphify_tool_command_unavailable"
    return path, None


def _graphify_resolve_uv_tool_graphify(
    uv_tool_bin_dir: Path,
    uv_tool_root_dir: Path,
) -> Path | None:
    candidates = [uv_tool_bin_dir / "graphify"]
    if os.name == "nt":
        candidates.append(uv_tool_bin_dir / "graphify.exe")
    for candidate in candidates:
        if not candidate.is_absolute():
            continue
        try:
            candidate_resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if os.name != "nt" and candidate.is_symlink():
            try:
                root_resolved = uv_tool_root_dir.resolve(strict=False)
            except OSError:
                continue
            if not _path_is_relative_to(candidate_resolved, root_resolved):
                continue
            if candidate_resolved.is_file() and os.access(candidate_resolved, os.X_OK):
                return candidate_resolved
            continue
        executable = _graphify_validate_executable(candidate, uv_tool_bin_dir)
        if executable is not None:
            return executable
    return None


def _graphify_resolve_graphify_cli(
    status_lines: list[str], uv_executable: Path
) -> tuple[Path | None, str | None]:
    uv_tool_bin_dir, reason = _graphify_resolve_uv_tool_dir(
        uv_executable,
        GRAPHIFY_UV_TOOL_BIN_DIR_COMMAND,
    )
    if reason is not None or uv_tool_bin_dir is None:
        status_lines.append("step=resolve_graphify_tool_bin_dir status=failed")
        return None, reason or "graphify_cli_command_unavailable"
    status_lines.append("step=resolve_graphify_tool_bin_dir status=done")

    uv_tool_root_dir, reason = _graphify_resolve_uv_tool_dir(
        uv_executable,
        GRAPHIFY_UV_TOOL_ROOT_DIR_COMMAND,
    )
    if reason is not None or uv_tool_root_dir is None:
        status_lines.append("step=resolve_graphify_tool_root_dir status=failed")
        return None, reason or "graphify_cli_command_unavailable"
    status_lines.append("step=resolve_graphify_tool_root_dir status=done")

    graphify_executable = _graphify_resolve_uv_tool_graphify(
        uv_tool_bin_dir,
        uv_tool_root_dir,
    )
    if graphify_executable is None:
        status_lines.append("step=resolve_graphify_cli status=failed")
        return None, "graphify_cli_command_unavailable"
    status_lines.append("step=resolve_graphify_cli status=done")
    return graphify_executable, None


def _graphify_uv_version_is_exact(output: str) -> bool:
    return re.search(
        rf"(?m)^uv\s+{re.escape(UV_PINNED_VERSION)}(?:\s|$)",
        output,
    ) is not None


def _graphify_verify_uv_executable(executable: Path) -> tuple[bool, str | None]:
    code, output, reason = _run_graphify_runtime_command(
        [str(executable), "--version"],
        command_unavailable_reason="graphify_tool_command_unavailable",
        cwd=ROOT,
    )
    if reason is not None:
        return False, reason
    if code != 0 or not _graphify_uv_version_is_exact(output):
        return False, GRAPHIFY_UV_VERSION_MISMATCH_REASON
    return True, None


def _graphify_resolve_verified_uv(status_lines: list[str]) -> tuple[Path | None, str | None]:
    path_uv = _graphify_resolve_path_executable("uv")
    path_uv_reason: str | None = None
    if path_uv is not None:
        exact, path_uv_reason = _graphify_verify_uv_executable(path_uv)
        if exact:
            status_lines.extend(
                (
                    "step=verify_path_uv status=done",
                    "step=resolve_pinned_uv_tool status=done",
                )
            )
            return path_uv, None
        status_lines.append("step=verify_path_uv status=failed")

    user_uv = _graphify_resolve_user_script("uv")
    if user_uv is not None:
        exact, reason = _graphify_verify_uv_executable(user_uv)
        if exact:
            status_lines.extend(
                (
                    "step=verify_user_uv status=done",
                    "step=resolve_pinned_uv_tool status=done",
                )
            )
            return user_uv, None
        status_lines.append("step=verify_user_uv status=failed")
    else:
        status_lines.append("step=verify_user_uv status=missing")

    if path_uv is not None and path_uv_reason != GRAPHIFY_UV_VERSION_MISMATCH_REASON:
        return None, path_uv_reason

    uv_executable, reason = _graphify_bootstrap_uv(status_lines)
    if reason is not None or uv_executable is None:
        return None, reason or "graphify_tool_command_unavailable"
    exact, reason = _graphify_verify_uv_executable(uv_executable)
    if exact:
        status_lines.append("step=verify_bootstrapped_user_uv status=done")
        return uv_executable, None
    status_lines.append("step=verify_bootstrapped_user_uv status=failed")
    return None, reason or GRAPHIFY_UV_VERSION_MISMATCH_REASON


def _graphify_command_exception_reason(
    error: Exception, command_unavailable_reason: str
) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return GRAPHIFY_COMMAND_TIMEOUT_REASON
    if isinstance(error, PermissionError):
        return GRAPHIFY_COMMAND_PERMISSION_REASON
    if isinstance(error, FileNotFoundError):
        return command_unavailable_reason
    if isinstance(error, (OSError, subprocess.SubprocessError)):
        return GRAPHIFY_COMMAND_LAUNCH_REASON
    return GRAPHIFY_RUNTIME_UNEXPECTED_FAILURE_REASON


def _run_graphify_runtime_command(
    command: list[str],
    *,
    command_unavailable_reason: str,
    cwd: str | Path | None = None,
    timeout: int = GRAPHIFY_RUNTIME_COMMAND_TIMEOUT_SECONDS,
) -> tuple[int | None, str, str | None]:
    try:
        code, output = run_command(command, cwd=cwd, timeout=timeout)
    except Exception as error:
        return None, "", _graphify_command_exception_reason(
            error,
            command_unavailable_reason,
        )
    return code, output, None


def _graphify_cli_contract_preflight(
    status_lines: list[str],
    graphify_executable: Path,
) -> str | None:
    checks = (
        (
            "verify_graphify_version",
            _graphify_command_with_executable(
                GRAPHIFY_VERSION_COMMAND,
                graphify_executable,
            ),
            lambda output: GRAPHIFY_PINNED_VERSION in output,
        ),
        (
            "verify_graphify_install_platform_help",
            _graphify_command_with_executable(
                GRAPHIFY_INSTALL_HELP_COMMAND,
                graphify_executable,
            ),
            lambda output: "--platform" in output,
        ),
        (
            "verify_graphify_folder_build_help",
            _graphify_command_with_executable(
                GRAPHIFY_BUILD_HELP_COMMAND,
                graphify_executable,
            ),
            lambda output: "ingest" not in output
            and ("folder" in output.lower() or "path" in output.lower()),
        ),
    )
    for step, command, accepts in checks:
        code, output, reason = _run_graphify_runtime_command(
            command,
            command_unavailable_reason="graphify_cli_command_unavailable",
        )
        if reason is not None:
            status_lines.append(f"step={step} status=failed")
            return reason
        if code != 0 or not accepts(output):
            status_lines.append(f"step={step} status=failed")
            return "graphify_cli_contract_unverified"
        status_lines.append(f"step={step} status=done")
    return None


def _write_graphify_synthetic_corpus(corpus_dir: Path) -> None:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "synthetic_module.py").write_text(
        "\n".join(
            (
                "def synthetic_total(values):",
                "    total = 0",
                "    for value in values:",
                "        total += value",
                "    return total",
                "",
            )
        ),
        encoding="utf-8",
    )


def _graphify_ast_smoke_command(
    corpus_dir: Path, output_dir: Path, home_dir: Path, graphify_executable: Path
) -> list[str]:
    return [
        "env",
        "-i",
        f"HOME={home_dir}",
        f"GRAPHIFY_OUT={output_dir}",
        "GRAPHIFY_DISABLE_NETWORK=1",
        "GRAPHIFY_DISABLE_HOOKS=1",
        "GRAPHIFY_DISABLE_SERVICES=1",
        "GRAPHIFY_DISABLE_PRIVATE_INDEXING=1",
        "PYTHONNOUSERSITE=1",
        "NO_COLOR=1",
        str(graphify_executable),
        str(corpus_dir),
    ]


def _run_graphify_smoke_command(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=GRAPHIFY_SMOKE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    except PermissionError:
        return 126, ""
    except FileNotFoundError:
        return 127, ""
    except (OSError, subprocess.SubprocessError):
        return 125, ""
    return result.returncode, result.stdout + result.stderr


def _graphify_graph_json_counts(path: Path) -> tuple[int, int] | None:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None

    nodes = parsed.get("nodes", parsed.get("node_count"))
    edges = parsed.get("edges", parsed.get("links", parsed.get("edge_count")))
    node_count = len(nodes) if isinstance(nodes, list) else nodes
    edge_count = len(edges) if isinstance(edges, list) else edges
    if isinstance(node_count, int) and isinstance(edge_count, int):
        return node_count, edge_count
    return None


def _run_graphify_ast_smoke(status_lines: list[str], graphify_executable: Path) -> str | None:
    with tempfile.TemporaryDirectory(prefix="skeleton-graphify-smoke-") as tmp:
        tmp_path = Path(tmp)
        corpus_dir = tmp_path / "synthetic-corpus"
        output_dir = tmp_path / "graphify-out"
        home_dir = tmp_path / "home"
        home_dir.mkdir(mode=0o700)
        _write_graphify_synthetic_corpus(corpus_dir)
        command = _graphify_ast_smoke_command(
            corpus_dir,
            output_dir,
            home_dir,
            graphify_executable,
        )
        code, _output = _run_graphify_smoke_command(command)
        if code == 124:
            status_lines.append("step=run_synthetic_ast_smoke status=timeout")
            return "synthetic_ast_smoke_timeout"
        if code == 126:
            status_lines.append("step=run_synthetic_ast_smoke status=failed")
            return GRAPHIFY_COMMAND_PERMISSION_REASON
        if code == 127:
            status_lines.append("step=run_synthetic_ast_smoke status=failed")
            return "graphify_cli_command_unavailable"
        if code == 125:
            status_lines.append("step=run_synthetic_ast_smoke status=failed")
            return GRAPHIFY_COMMAND_LAUNCH_REASON
        if code != 0:
            status_lines.append("step=run_synthetic_ast_smoke status=failed")
            return "synthetic_ast_smoke_failed"
        counts = _graphify_graph_json_counts(corpus_dir / "graphify-out" / "graph.json")
        if counts is None:
            status_lines.append("step=verify_synthetic_graph_json status=failed")
            return "synthetic_graph_json_missing_or_invalid"
        node_count, edge_count = counts
        if node_count <= 0 or edge_count <= 0:
            status_lines.append("step=verify_synthetic_graph_json status=failed")
            return "synthetic_graph_json_empty"
        status_lines.extend(
            (
                "step=run_synthetic_ast_smoke status=done",
                "step=verify_synthetic_graph_json status=done",
                f"synthetic_graph_node_count={node_count}",
                f"synthetic_graph_edge_count={edge_count}",
            )
        )
    return None


def install_graphify_runtime(body: str) -> str:
    task_id = INSTALL_GRAPHIFY_RUNTIME
    approval = _body_field(body, "Operator Approval")
    status_lines = [
        f"uv_version={UV_PINNED_VERSION}",
        f"graphify_version={GRAPHIFY_PINNED_VERSION}",
        f"synthetic_smoke_timeout_seconds={GRAPHIFY_SMOKE_TIMEOUT_SECONDS}",
        f"runtime_command_timeout_seconds={GRAPHIFY_RUNTIME_COMMAND_TIMEOUT_SECONDS}",
        f"python_package_tooling_timeout_seconds={GRAPHIFY_PACKAGE_TOOLING_TIMEOUT_SECONDS}",
        f"user_bootstrap_timeout_seconds={GRAPHIFY_USER_BOOTSTRAP_TIMEOUT_SECONDS}",
        "network_disabled=true",
        "hooks_disabled=true",
        "services_disabled=true",
        "ports_disabled=true",
        "private_indexing_disabled=true",
        "model_credentials_removed_from_smoke=true",
        f"managed_profile_root_count={len(GRAPHIFY_PROFILE_ROOT_RELATIVE_PATHS)}",
        f"managed_version_marker_count={_graphify_existing_version_marker_count()}",
    ]
    if approval != GRAPHIFY_RUNTIME_APPROVAL:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=missing_operator_approval"],
            "not_met",
    )
    status_lines.append("approval_status=verified")

    uv_executable, reason = _graphify_resolve_verified_uv(status_lines)
    if reason is not None or uv_executable is None:
        return _graphify_runtime_blocked_report(
            [*status_lines, "step=select_verified_uv status=failed"],
            reason or GRAPHIFY_UV_VERSION_MISMATCH_REASON,
        )
    status_lines.append("step=select_verified_uv status=done")

    code, _output, reason = _run_graphify_runtime_command(
        _graphify_command_with_executable(GRAPHIFY_TOOL_INSTALL_COMMAND, uv_executable),
        command_unavailable_reason="graphify_tool_command_unavailable",
        cwd=ROOT,
    )
    if reason is not None:
        return _graphify_runtime_blocked_report(
            [*status_lines, "step=install_pinned_graphify_tool status=failed"],
            reason,
        )
    if code != 0:
        return _graphify_runtime_blocked_report(
            [*status_lines, "step=install_pinned_graphify_tool status=failed"],
            "graphify_tool_install_failed",
        )
    status_lines.append("step=install_pinned_graphify_tool status=done")

    try:
        graphify_executable, reason = _graphify_resolve_graphify_cli(
            status_lines,
            uv_executable,
        )
        if reason is not None or graphify_executable is None:
            return _graphify_runtime_blocked_report(
                status_lines,
                reason or "graphify_cli_command_unavailable",
            )
        reason = _graphify_cli_contract_preflight(status_lines, graphify_executable)
        if reason is not None:
            return _graphify_runtime_blocked_report(status_lines, reason)

        backup, backup_status, backup_item_count = _backup_graphify_profiles()
    except Exception:
        return _graphify_runtime_blocked_report(
            status_lines,
            GRAPHIFY_RUNTIME_UNEXPECTED_FAILURE_REASON,
        )
    status_lines.extend(
        (
            f"profile_backup_status={backup_status}",
            f"profile_backup_item_count={backup_item_count}",
            "profile_backup_private=true",
        )
    )
    if backup is None:
        return _graphify_runtime_blocked_report(status_lines, "profile_backup_failed")

    try:
        code, _output, reason = _run_graphify_runtime_command(
            _graphify_command_with_executable(
                GRAPHIFY_CODEX_SKILL_INSTALL_COMMAND,
                graphify_executable,
            ),
            command_unavailable_reason="graphify_cli_command_unavailable",
            cwd=ROOT,
        )
        if reason is not None:
            status_lines.append("step=install_codex_skill status=failed")
            rollback_status = _restore_graphify_profiles_safely(backup)
            return _graphify_runtime_blocked_report(
                status_lines,
                reason,
                rollback_status,
            )
        if code != 0:
            status_lines.append("step=install_codex_skill status=failed")
            rollback_status = _restore_graphify_profiles_safely(backup)
            return _graphify_runtime_blocked_report(
                status_lines,
                "codex_skill_install_failed",
                rollback_status,
            )
        status_lines.append("step=install_codex_skill status=done")

        code, _output, reason = _run_graphify_runtime_command(
            _graphify_command_with_executable(
                GRAPHIFY_HERMES_SKILL_INSTALL_COMMAND,
                graphify_executable,
            ),
            command_unavailable_reason="graphify_cli_command_unavailable",
            cwd=ROOT,
        )
        if reason is not None:
            status_lines.append("step=install_hermes_skill status=failed")
            rollback_status = _restore_graphify_profiles_safely(backup)
            return _graphify_runtime_blocked_report(
                status_lines,
                reason,
                rollback_status,
            )
        if code != 0:
            status_lines.append("step=install_hermes_skill status=failed")
            rollback_status = _restore_graphify_profiles_safely(backup)
            return _graphify_runtime_blocked_report(
                status_lines,
                "hermes_skill_install_failed",
                rollback_status,
            )
        status_lines.append("step=install_hermes_skill status=done")

        reason = _run_graphify_ast_smoke(status_lines, graphify_executable)
        if reason is not None:
            rollback_status = _restore_graphify_profiles_safely(backup)
            return _graphify_runtime_blocked_report(
                status_lines,
                reason,
                rollback_status,
            )
    except Exception:
        rollback_status = _restore_graphify_profiles_safely(backup)
        return _graphify_runtime_blocked_report(
            status_lines,
            GRAPHIFY_RUNTIME_UNEXPECTED_FAILURE_REASON,
            rollback_status,
        )

    status_lines.extend(
        (
            "installed_skill_platform_count=2",
            "synthetic_corpus_status=temporary_cleaned",
            "recovery_snapshot_status=retained",
            "rollback_status=not_needed",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _aufmass_private_registered_paths() -> tuple[Path | None, Path | None, str | None]:
    project = load_runner_project_tree().get("projects", {}).get(
        AUFMASS_PRIVATE_PROJECT_ID
    )
    if not isinstance(project, dict):
        return None, None, "reason=private_project_not_registered"
    if project.get("repo") != AUFMASS_PRIVATE_REGISTERED_REPO:
        return None, None, "reason=private_project_repo_mismatch"
    if project.get("public") is not False:
        return None, None, "reason=private_project_visibility_mismatch"

    checkout_path = Path(str(project.get("checkout_path") or ""))
    workspace_root = Path(str(project.get("worktree_root") or ""))
    for name, path in (
        ("checkout_path", checkout_path),
        ("private_workspace", workspace_root),
    ):
        if not path.is_absolute():
            return None, None, f"reason={name}_not_absolute"
        if any(part == ".." for part in path.parts):
            return None, None, f"reason={name}_traversal"
        if not _path_is_under_allowed_target_base(path):
            return None, None, f"reason={name}_unsafe"

    if workspace_root == ROOT or _path_is_relative_to(workspace_root, ROOT):
        return None, None, "reason=private_workspace_inside_public_repo"
    if checkout_path == ROOT or _path_is_relative_to(checkout_path, ROOT):
        return None, None, "reason=private_checkout_inside_public_repo"

    return checkout_path, workspace_root, None


def _python_import_check_script(modules: tuple[str, ...]) -> str:
    return (
        "import importlib.util, sys\n"
        f"modules={modules!r}\n"
        "missing=[module for module in modules if importlib.util.find_spec(module) is None]\n"
        "sys.exit(1 if missing else 0)\n"
    )


def prepare_aufmass_private_runtime() -> str:
    task_id = PREPARE_AUFMASS_PRIVATE_RUNTIME
    status_lines = [
        f"target_project={AUFMASS_PRIVATE_PROJECT_ID}",
        "private_workspace=registered",
        "report_private_paths=false",
        "report_drawings=false",
        "report_quantities=false",
        "mutation_mode=none",
    ]
    checkout_path, workspace_root, reason = _aufmass_private_registered_paths()
    if reason is not None or checkout_path is None or workspace_root is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, reason or "reason=private_project_unavailable"],
            "not_met",
        )

    pilot_script = ROOT / "scripts" / "aufmass_private_pilot_run.py"
    source_pack_manifest = workspace_root / AUFMASS_PRIVATE_SOURCE_PACK_MANIFEST
    bounded_checks = (
        ("private_checkout", checkout_path.is_dir()),
        ("private_checkout_git", (checkout_path / ".git").exists()),
        ("private_workspace", workspace_root.is_dir()),
        ("source_pack_manifest", source_pack_manifest.is_file()),
        ("pilot_script", pilot_script.is_file()),
        ("tool_python3", shutil.which("python3") is not None),
    )
    for step, ok in bounded_checks:
        status_lines.append(f"step=verify_{step} status={'done' if ok else 'failed'}")
        if not ok:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [*status_lines, f"reason={step}_unavailable"],
                "not_met",
            )

    dependency_checks = (
        ("required_python_modules", AUFMASS_PRIVATE_REQUIRED_MODULES),
        ("dxf_python_module", (AUFMASS_PRIVATE_OPTIONAL_DXF_MODULE,)),
    )
    for step, modules in dependency_checks:
        code, _output = run_command(
            ["python3", "-c", _python_import_check_script(modules)],
            cwd=ROOT,
        )
        status_lines.append(
            f"step=verify_{step} status={'done' if code == 0 else 'failed'}"
        )
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [*status_lines, f"reason={step}_missing"],
                "not_met",
            )

    code, output = run_command(
        [
            "python3",
            "-m",
            "scripts.aufmass_private_pilot_run",
            "--source-pack-manifest",
            str(source_pack_manifest),
            "--branch",
            "manual-only",
        ],
        cwd=ROOT,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"step=dry_run_private_pilot status=failed exit_code={code}"],
            "not_met",
        )

    try:
        dry_run_summary = json.loads(output or "{}")
    except json.JSONDecodeError:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=parse_dry_run_summary status=failed"],
            "not_met",
        )
    if (
        not isinstance(dry_run_summary, dict)
        or dry_run_summary.get("mode") != "dry-run"
        or dry_run_summary.get("schema")
        != "skeleton.aufmass_private_pilot_public_summary.v1"
    ):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_dry_run_summary status=failed"],
            "not_met",
        )

    status_lines.extend(
        (
            "step=dry_run_private_pilot status=done",
            "step=parse_dry_run_summary status=done",
            "step=verify_dry_run_summary status=done",
            "pilot_mode=dry-run",
            "pilot_summary_schema=skeleton.aufmass_private_pilot_public_summary.v1",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _aufmass_private_metadata(body: str) -> str:
    return (body or "").split("```task", 1)[0]


def _valid_aufmass_private_token(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and _AUFMASS_PRIVATE_TOKEN_RE.fullmatch(value) is not None
    )


def _validate_aufmass_private_issue_metadata(
    task_id: str, body: str, *, include_mode: bool
) -> str | None:
    allowed_fields = {"Mode", "Maintenance Task ID", "Private Source Pack ID", "Run ID"}
    if include_mode:
        allowed_fields.add("Pilot Mode")
    for line in _aufmass_private_metadata(body).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        field, separator, _value = stripped.partition(":")
        if not separator or field not in allowed_fields:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                ["reason=unsupported_private_aufmass_issue_field"],
                "not_met",
            )
    return None


def _aufmass_private_automation_request(
    task_id: str, body: str, *, include_mode: bool
) -> tuple[AufmassPrivateAutomationRequest | None, str | None]:
    metadata_report = _validate_aufmass_private_issue_metadata(
        task_id, body, include_mode=include_mode
    )
    if metadata_report is not None:
        return None, metadata_report

    metadata = _aufmass_private_metadata(body)
    source_pack_id = _body_field(metadata, "Private Source Pack ID")
    if not _valid_aufmass_private_token(source_pack_id):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=invalid_private_source_pack_id"],
            "not_met",
        )

    run_id = _body_field(metadata, "Run ID")
    if run_id is not None and not _valid_aufmass_private_token(run_id):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [f"source_pack_id={source_pack_id}", "reason=invalid_run_id"],
            "not_met",
        )

    mode = "dry-run"
    if include_mode:
        mode = _body_field(metadata, "Pilot Mode") or "dry-run"
        if mode not in {"dry-run", "execute"}:
            return None, _maintenance_report(
                "BLOCKED",
                task_id,
                [f"source_pack_id={source_pack_id}", "reason=invalid_pilot_mode"],
                "not_met",
            )

    return AufmassPrivateAutomationRequest(source_pack_id, mode, run_id), None


def _read_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "json_read_failed"
    if not isinstance(data, dict):
        return None, "json_not_object"
    return data, None


def _resolve_private_registry_path(
    workspace_root: Path,
) -> tuple[Path | None, str | None]:
    registry_path = workspace_root / AUFMASS_PRIVATE_AUTOMATION_REGISTRY
    resolved_registry = registry_path.resolve(strict=False)
    if not _path_is_relative_to(resolved_registry, workspace_root):
        return None, "registry_outside_private_workspace"
    if resolved_registry == ROOT or _path_is_relative_to(resolved_registry, ROOT):
        return None, "registry_inside_public_repo"
    if not resolved_registry.is_file():
        return None, "registry_missing"
    return resolved_registry, None


def _private_registry_relative_path(
    workspace_root: Path, raw_path: object, label: str
) -> tuple[Path | None, str | None]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, f"{label}_missing"
    if "://" in raw_path or "\\" in raw_path:
        return None, f"{label}_unsafe"
    candidate = Path(raw_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        return None, f"{label}_unsafe"
    resolved = (workspace_root / candidate).resolve(strict=False)
    if not _path_is_relative_to(resolved, workspace_root):
        return None, f"{label}_outside_private_workspace"
    if resolved == ROOT or _path_is_relative_to(resolved, ROOT):
        return None, f"{label}_inside_public_repo"
    return resolved, None


def _source_pack_registry_entry(data: dict[str, Any], source_pack_id: str) -> dict[str, Any] | None:
    source_packs = data.get("source_packs")
    if isinstance(source_packs, dict):
        entry = source_packs.get(source_pack_id)
        return entry if isinstance(entry, dict) else None
    return None


def _resolve_aufmass_private_registry_entry(
    task_id: str,
    request: AufmassPrivateAutomationRequest,
    workspace_root: Path,
) -> tuple[AufmassPrivateRegistryEntry | None, str | None]:
    registry_path, reason = _resolve_private_registry_path(workspace_root)
    status_prefix = [f"source_pack_id={request.source_pack_id}"]
    if reason is not None or registry_path is None:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_prefix, f"reason={reason or 'registry_unavailable'}"],
            "not_met",
        )

    registry, reason = _read_json_file(registry_path)
    if reason is not None or registry is None:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_prefix, f"reason=registry_{reason or 'invalid'}"],
            "not_met",
        )
    if registry.get("schema") != AUFMASS_PRIVATE_AUTOMATION_REGISTRY_SCHEMA:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_prefix, "reason=registry_schema_invalid"],
            "not_met",
        )

    entry = _source_pack_registry_entry(registry, request.source_pack_id)
    if entry is None:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_prefix, "reason=source_pack_not_registered"],
            "not_met",
        )

    run_id = request.run_id
    output_root_value: object = entry.get("output_root")
    runs = entry.get("runs")
    if run_id is None:
        latest_run_id = entry.get("latest_run_id")
        if isinstance(latest_run_id, str) and _valid_aufmass_private_token(latest_run_id):
            run_id = latest_run_id
    if run_id is not None and isinstance(runs, dict):
        run_entry = runs.get(run_id)
        if not isinstance(run_entry, dict):
            return None, _maintenance_report(
                "BLOCKED",
                task_id,
                [*status_prefix, f"run_id={run_id}", "reason=run_not_registered"],
                "not_met",
            )
        output_root_value = run_entry.get("output_root", output_root_value)
    if run_id is None:
        run_id = "registry_default"

    manifest_path, reason = _private_registry_relative_path(
        workspace_root, entry.get("source_pack_manifest"), "source_pack_manifest"
    )
    if reason is None and manifest_path is not None and manifest_path.name != AUFMASS_PRIVATE_SOURCE_PACK_MANIFEST:
        reason = "source_pack_manifest_name_invalid"
    artifact_map_path, artifact_reason = _private_registry_relative_path(
        workspace_root, entry.get("artifact_map"), "artifact_map"
    )
    output_root, output_reason = _private_registry_relative_path(
        workspace_root, output_root_value, "output_root"
    )
    reason = reason or artifact_reason or output_reason
    if reason is not None or manifest_path is None or artifact_map_path is None or output_root is None:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_prefix, f"run_id={run_id}", f"reason={reason or 'registry_path_invalid'}"],
            "not_met",
        )
    return (
        AufmassPrivateRegistryEntry(
            source_pack_id=request.source_pack_id,
            manifest_path=manifest_path,
            artifact_map_path=artifact_map_path,
            output_root=output_root,
            run_id=run_id,
        ),
        None,
    )


def _load_and_validate_private_source_pack(
    task_id: str, request: AufmassPrivateAutomationRequest, entry: AufmassPrivateRegistryEntry
) -> tuple[dict[str, Any] | None, str | None]:
    source_pack_data, reason = _read_json_file(entry.manifest_path)
    if reason is not None or source_pack_data is None:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"source_pack_id={request.source_pack_id}",
                f"run_id={entry.run_id}",
                f"reason=source_pack_{reason or 'invalid'}",
            ],
            "not_met",
        )
    validation = validate_source_pack_manifest(source_pack_data)
    if not validation.ok:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"source_pack_id={request.source_pack_id}",
                f"run_id={entry.run_id}",
                f"source_pack_warning_count={len(validation.warnings)}",
                f"source_pack_error_count={len(validation.errors)}",
                "reason=source_pack_validation_failed",
            ],
            "not_met",
        )
    return source_pack_data, None


def _source_pack_count(source_pack_data: dict[str, Any], source_type: str | None = None) -> int:
    sources = source_pack_data.get("sources")
    if not isinstance(sources, list):
        return 0
    if source_type is None:
        return len([source for source in sources if isinstance(source, dict)])
    return len(
        [
            source
            for source in sources
            if isinstance(source, dict) and source.get("source_type") == source_type
        ]
    )


def _warning_count(summary: dict[str, Any]) -> int:
    source_validation = summary.get("source_validation")
    if not isinstance(source_validation, dict):
        return 0
    warnings = source_validation.get("warnings")
    return len(warnings) if isinstance(warnings, list) else 0


def _summary_int(summary: dict[str, Any], key: str, default: int = 0) -> int:
    value = summary.get(key)
    return value if isinstance(value, int) and value >= 0 else default


def _artifact_count(summary: dict[str, Any]) -> int:
    value = summary.get("artifact_count")
    if isinstance(value, int) and value >= 0:
        return value
    artifacts = summary.get("private_artifacts")
    return len(artifacts) if isinstance(artifacts, list) else 0


def run_aufmass_private_dxf_review(body: str) -> str:
    task_id = RUN_AUFMASS_PRIVATE_DXF_REVIEW
    request, report = _aufmass_private_automation_request(task_id, body, include_mode=True)
    if report is not None:
        return report
    assert request is not None

    checkout_path, workspace_root, reason = _aufmass_private_registered_paths()
    if reason is not None or checkout_path is None or workspace_root is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [f"source_pack_id={request.source_pack_id}", reason or "reason=private_project_unavailable"],
            "not_met",
        )

    entry, report = _resolve_aufmass_private_registry_entry(task_id, request, workspace_root)
    if report is not None:
        return report
    assert entry is not None

    source_pack_data, report = _load_and_validate_private_source_pack(task_id, request, entry)
    if report is not None:
        return report
    assert source_pack_data is not None
    validation = validate_source_pack_manifest(source_pack_data)
    dxf_source_count = _source_pack_count(source_pack_data, "dxf")
    selected_source_count = dxf_source_count

    command = [
        "python3",
        "-m",
        "scripts.aufmass_private_pilot_run",
        "--source-pack-manifest",
        str(entry.manifest_path),
        "--branch",
        "dxf-assisted",
    ]
    if request.mode == "execute":
        command.extend(
            [
                "--execute",
                "--private-workspace",
                str(workspace_root),
                "--output-root",
                str(entry.output_root),
                "--artifact-map",
                str(entry.artifact_map_path),
            ]
        )
    code, output = run_command(command, cwd=ROOT)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"source_pack_id={request.source_pack_id}",
                f"mode={request.mode}",
                "branch=dxf-assisted",
                f"run_id={entry.run_id}",
                f"warning_count={len(validation.warnings)}",
                f"step=run_private_pilot status=failed exit_code={code}",
            ],
            "not_met",
        )
    try:
        summary = json.loads(output or "{}")
    except json.JSONDecodeError:
        summary = {}
    if (
        not isinstance(summary, dict)
        or summary.get("schema") != "skeleton.aufmass_private_pilot_public_summary.v1"
        or summary.get("mode") != request.mode
        or summary.get("branch") != "dxf-assisted"
    ):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"source_pack_id={request.source_pack_id}",
                f"mode={request.mode}",
                "branch=dxf-assisted",
                f"run_id={entry.run_id}",
                "step=verify_public_summary status=failed",
            ],
            "not_met",
        )

    status_lines = [
        "status=done",
        f"source_pack_id={request.source_pack_id}",
        f"mode={request.mode}",
        "branch=dxf-assisted",
        f"selected_source_count={_summary_int(summary, 'selected_source_count', selected_source_count)}",
        f"dxf_source_count={_summary_int(summary, 'dxf_source_count', dxf_source_count)}",
        f"artifact_count={_artifact_count(summary)}",
        f"run_id={entry.run_id}",
        f"warning_count={_warning_count(summary)}",
        f"source_pack_warning_count={len(validation.warnings)}",
    ]
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _review_table_private_paths(output_root: Path) -> list[Path]:
    if not output_root.is_dir():
        return []
    return sorted(output_root.glob("*_room_review_table.json"))


def _summarize_review_table(path: Path) -> tuple[int, dict[str, int], int]:
    data, reason = _read_json_file(path)
    if reason is not None or data is None:
        return 0, {}, 1
    rows = data.get("rows")
    if not isinstance(rows, list):
        return 0, {}, 1
    status_counts: dict[str, int] = {}
    source_tokens: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = row.get("review_status")
        if isinstance(status, str) and _valid_aufmass_private_token(status):
            status_counts[status] = status_counts.get(status, 0) + 1
        source_ref = row.get("source_ref")
        if isinstance(source_ref, str) and _valid_aufmass_private_token(source_ref):
            source_tokens.add(source_ref)
    return len(rows), status_counts, len(source_tokens)


def _private_shortlist_artifact_paths(output_root: Path) -> tuple[Path, Path]:
    return (
        output_root / "aufmass_private_shortlist.json",
        output_root / "aufmass_private_shortlist.csv",
    )


def _private_area_schedule_artifact_paths(output_root: Path) -> tuple[Path, Path, Path]:
    return (
        output_root / "aufmass_private_area_schedule.json",
        output_root / "aufmass_private_room_area_schedule.csv",
        output_root / "aufmass_private_wall_area_schedule.csv",
    )


def _private_row_has_text(row: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _private_row_text(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


_PRIVATE_WEAK_STATUS_VALUES = {
    "area_mismatch",
    "candidate",
    "contour",
    "fallback",
    "needs_review",
    "weak",
}
_PRIVATE_STRONG_STATUS_VALUES = {"approved", "strong"}
_PRIVATE_REVIEW_STATUS_KEYS = (
    "row_type",
    "type",
    "category",
    "review_status",
    "filtering_reason",
    "status",
    "match_status",
    "area_status",
    "confidence",
    "confidence_status",
    "source_status",
    "row_status",
    "classification",
    "source_kind",
)
_PRIVATE_ROOM_REF_KEYS = ("room_ref", "room_id", "room_label", "room_name", "room")
_PRIVATE_WALL_REF_KEYS = ("wall_ref", "wall_id", "wall_label", "wall_name", "wall")
_PRIVATE_GENERIC_AREA_KEYS = ("area_m2", "area", "surface_area", "detected_area_m2")


def _private_status_values(row: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in _PRIVATE_REVIEW_STATUS_KEYS:
        value = row.get(key)
        if not isinstance(value, str):
            continue
        normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
        if not normalized:
            continue
        values.add(normalized)
        values.update(part for part in normalized.split("_") if part)
    return values


def _private_row_has_weak_status(row: dict[str, Any]) -> bool:
    values = _private_status_values(row)
    return bool(values & _PRIVATE_WEAK_STATUS_VALUES)


def _private_row_has_strong_status(row: dict[str, Any]) -> bool:
    values = _private_status_values(row)
    return bool(values & _PRIVATE_STRONG_STATUS_VALUES)


def _private_row_has_area(row: dict[str, Any]) -> bool:
    for key in ("area_m2", "area", "surface_area", "detected_area_m2"):
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return True
        if isinstance(value, str):
            try:
                if float(value.strip()) > 0:
                    return True
            except ValueError:
                continue
    return False


def _private_positive_float(row: dict[str, Any], keys: tuple[str, ...]) -> tuple[float | None, str | None]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return float(value), key
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                continue
            if parsed > 0:
                return parsed, key
    return None, None


def _private_nonnegative_float(
    row: dict[str, Any], keys: tuple[str, ...]
) -> tuple[float | None, str | None]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value >= 0:
            return float(value), key
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                continue
            if parsed >= 0:
                return parsed, key
    return None, None


def _private_row_is_candidate_contour(row: dict[str, Any]) -> bool:
    return _private_row_has_weak_status(row)


def _private_evidence(
    source: str | None, status: str, confidence: str = "sufficient"
) -> dict[str, str]:
    return {
        "source": source or "missing",
        "status": status,
        "confidence": confidence,
    }


def _private_room_area_schedule_row(
    table_index: int, row_index: int, row: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    if _private_row_is_candidate_contour(row):
        return None, "weak_review_status_not_payable_quantity"
    floor_area, floor_source = _private_positive_float(
        row,
        (
            "floor_area_m2",
            "room_floor_area_m2",
            "floor_area",
        ),
    )
    if floor_area is None and _private_row_has_strong_status(row):
        floor_area, floor_source = _private_positive_float(row, _PRIVATE_GENERIC_AREA_KEYS)
    if floor_area is None:
        return None, "missing_floor_area_evidence"
    ceiling_area, ceiling_source = _private_positive_float(
        row, ("ceiling_area_m2", "room_ceiling_area_m2", "ceiling_area")
    )
    ceiling_status = "explicit"
    ceiling_confidence = "sufficient"
    if ceiling_area is None:
        ceiling_area = floor_area
        ceiling_source = floor_source
        ceiling_status = "assumed_equal_to_floor_area"
        ceiling_confidence = "assumed"
    floor_status = "explicit"
    if floor_source in _PRIVATE_GENERIC_AREA_KEYS:
        floor_status = "explicit_generic_area_used_as_floor_area"
    return {
        "table_index": table_index,
        "row_index": row_index,
        "room_ref": _private_row_text(row, _PRIVATE_ROOM_REF_KEYS) or "",
        "floor_area_m2": round(floor_area, 6),
        "ceiling_area_m2": round(ceiling_area, 6),
        "evidence": {
            "floor_area_m2": _private_evidence(floor_source, floor_status),
            "ceiling_area_m2": _private_evidence(
                ceiling_source, ceiling_status, ceiling_confidence
            ),
        },
    }, None


def _private_wall_area_schedule_row(
    table_index: int, row_index: int, row: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    if _private_row_is_candidate_contour(row):
        return None, "weak_review_status_not_payable_quantity"
    wall_length, length_source = _private_positive_float(
        row, ("wall_length_m", "length_m", "wall_length", "length")
    )
    height, height_source = _private_positive_float(row, ("height_m", "wall_height_m", "height"))
    opening_area, opening_source = _private_nonnegative_float(
        row, ("opening_area_m2", "openings_area_m2", "opening_area")
    )
    if wall_length is None:
        return None, "missing_wall_length_evidence"
    if height is None:
        return None, "missing_wall_height_evidence"
    opening_status = "explicit"
    opening_confidence = "sufficient"
    if opening_area is None:
        opening_area = 0.0
        opening_source = "missing"
        opening_status = "assumed_zero"
        opening_confidence = "assumed"
    elif opening_area == 0:
        opening_status = "known_zero"
    else:
        opening_status = "known_value"
    gross_wall_area = wall_length * height
    net_wall_area = gross_wall_area - opening_area
    if net_wall_area < 0:
        return None, "opening_area_exceeds_gross_wall_area"
    return {
        "table_index": table_index,
        "row_index": row_index,
        "wall_ref": _private_row_text(row, _PRIVATE_WALL_REF_KEYS) or "",
        "wall_length_m": round(wall_length, 6),
        "height_m": round(height, 6),
        "gross_wall_area_m2": round(gross_wall_area, 6),
        "opening_area_m2": round(opening_area, 6),
        "opening_area_status": opening_status,
        "net_wall_area_m2": round(net_wall_area, 6),
        "evidence": {
            "wall_length_m": _private_evidence(length_source, "explicit"),
            "height_m": _private_evidence(height_source, "explicit"),
            "opening_area_m2": _private_evidence(
                opening_source, opening_status, opening_confidence
            ),
            "gross_wall_area_m2": _private_evidence(
                "wall_length_m,height_m", "calculated_from_explicit_length_and_height"
            ),
            "net_wall_area_m2": _private_evidence(
                "gross_wall_area_m2,opening_area_m2",
                "calculated_from_explicit_gross_and_opening_area",
            ),
        },
    }, None


def _private_shortlist_row(
    table_index: int, row_index: int, row: dict[str, Any]
) -> dict[str, Any]:
    has_room = _private_row_has_text(row, ("room_label", "room_name", "room"))
    has_label = _private_row_has_text(row, ("label", "item_label", "name"))
    has_area = _private_row_has_area(row)
    evidence_score = int(has_room) + int(has_label) + int(has_area)
    reasons = []
    if not has_room:
        reasons.append("missing_room_evidence")
    if not has_label:
        reasons.append("missing_label_evidence")
    if not has_area:
        reasons.append("missing_area_evidence")
    return {
        "table_index": table_index,
        "row_index": row_index,
        "evidence": {
            "has_room": has_room,
            "has_label": has_label,
            "has_area": has_area,
            "score": evidence_score,
        },
        "filtering_reason": "usable_evidence" if evidence_score >= 2 else ",".join(reasons),
        "row": row,
    }


def _read_private_review_rows(
    table_paths: list[Path],
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    warning_count = 0
    for table_index, table_path in enumerate(table_paths):
        data, reason = _read_json_file(table_path)
        if reason is not None or data is None:
            warning_count += 1
            continue
        table_rows = data.get("rows")
        if not isinstance(table_rows, list):
            warning_count += 1
            continue
        for row_index, row in enumerate(table_rows):
            if not isinstance(row, dict):
                warning_count += 1
                continue
            status = row.get("review_status")
            if isinstance(status, str) and _valid_aufmass_private_token(status):
                status_counts[status] = status_counts.get(status, 0) + 1
            rows.append(_private_shortlist_row(table_index, row_index, row))
    return rows, status_counts, warning_count


def _select_private_shortlist_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    max_rows = 100
    preferred = [
        row
        for row in rows
        if isinstance(row.get("evidence"), dict) and row["evidence"].get("score", 0) >= 2
    ]
    if not preferred:
        preferred = sorted(
            rows,
            key=lambda row: (
                row.get("evidence", {}).get("score", 0)
                if isinstance(row.get("evidence"), dict)
                else 0,
                -int(row.get("table_index", 0)),
                -int(row.get("row_index", 0)),
            ),
            reverse=True,
        )[:25]
        for row in preferred:
            row["filtering_reason"] = "fallback_no_rows_with_strong_evidence"
    return preferred[:max_rows]


def _write_private_shortlist_artifacts(
    entry: AufmassPrivateRegistryEntry,
    rows: list[dict[str, Any]],
    input_table_count: int,
    input_row_count: int,
    status_counts: dict[str, int],
    warning_count: int,
) -> str | None:
    resolved_output_root = entry.output_root.resolve(strict=False)
    json_path, csv_path = _private_shortlist_artifact_paths(resolved_output_root)
    for path in (json_path, csv_path):
        resolved = path.resolve(strict=False)
        if not _path_is_relative_to(resolved, resolved_output_root):
            return "shortlist_artifact_path_unsafe"
        if resolved == ROOT or _path_is_relative_to(resolved, ROOT):
            return "shortlist_artifact_inside_public_repo"
    payload = {
        "schema": "skeleton.aufmass_private_shortlist.v1",
        "source_pack_id": entry.source_pack_id,
        "run_id": entry.run_id,
        "input_table_count": input_table_count,
        "input_row_count": input_row_count,
        "shortlist_row_count": len(rows),
        "status_counts": status_counts,
        "warning_count": warning_count,
        "rows": rows,
    }
    try:
        entry.output_root.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=(
                    "table_index",
                    "row_index",
                    "evidence_score",
                    "has_room",
                    "has_label",
                    "has_area",
                    "filtering_reason",
                    "row_json",
                ),
            )
            writer.writeheader()
            for row in rows:
                evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
                writer.writerow(
                    {
                        "table_index": row.get("table_index"),
                        "row_index": row.get("row_index"),
                        "evidence_score": evidence.get("score", 0),
                        "has_room": str(bool(evidence.get("has_room"))).lower(),
                        "has_label": str(bool(evidence.get("has_label"))).lower(),
                        "has_area": str(bool(evidence.get("has_area"))).lower(),
                        "filtering_reason": row.get("filtering_reason", ""),
                        "row_json": json.dumps(
                            row.get("row", {}), ensure_ascii=False, sort_keys=True
                        ),
                    }
                )
    except OSError:
        return "shortlist_artifact_write_failed"
    return None


def _build_private_area_schedules(
    table_paths: list[Path],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    room_rows: list[dict[str, Any]] = []
    wall_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    warning_count = 0
    for table_index, table_path in enumerate(table_paths):
        data, reason = _read_json_file(table_path)
        if reason is not None or data is None:
            warning_count += 1
            diagnostics.append(
                {"table_index": table_index, "row_index": None, "reason": "table_read_failed"}
            )
            continue
        table_rows = data.get("rows")
        if not isinstance(table_rows, list):
            warning_count += 1
            diagnostics.append(
                {"table_index": table_index, "row_index": None, "reason": "table_rows_missing"}
            )
            continue
        for row_index, row in enumerate(table_rows):
            if not isinstance(row, dict):
                warning_count += 1
                diagnostics.append(
                    {
                        "table_index": table_index,
                        "row_index": row_index,
                        "reason": "row_not_object",
                    }
                )
                continue
            room_row, room_reason = _private_room_area_schedule_row(
                table_index, row_index, row
            )
            wall_row, wall_reason = _private_wall_area_schedule_row(
                table_index, row_index, row
            )
            if room_row is not None and wall_row is not None:
                diagnostics.append(
                    {
                        "table_index": table_index,
                        "row_index": row_index,
                        "reason": "ambiguous_room_wall_quantity",
                    }
                )
                continue
            if room_row is not None:
                room_rows.append(room_row)
            if wall_row is not None:
                wall_rows.append(wall_row)
            if room_row is None and wall_row is None:
                diagnostics.append(
                    {
                        "table_index": table_index,
                        "row_index": row_index,
                        "reason": ",".join(
                            sorted(
                                {
                                    reason
                                    for reason in (room_reason, wall_reason)
                                    if reason is not None
                                }
                            )
                        )
                        or "insufficient_area_schedule_evidence",
                    }
                )
    if not table_paths:
        diagnostics.append(
            {"table_index": None, "row_index": None, "reason": "review_table_missing"}
        )
    return room_rows, wall_rows, diagnostics, warning_count


def _write_private_area_schedule_artifacts(
    entry: AufmassPrivateRegistryEntry,
    room_rows: list[dict[str, Any]],
    wall_rows: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    warning_count: int,
) -> str | None:
    resolved_output_root = entry.output_root.resolve(strict=False)
    json_path, room_csv_path, wall_csv_path = _private_area_schedule_artifact_paths(
        resolved_output_root
    )
    for path in (json_path, room_csv_path, wall_csv_path):
        resolved = path.resolve(strict=False)
        if not _path_is_relative_to(resolved, resolved_output_root):
            return "area_schedule_artifact_path_unsafe"
        if resolved == ROOT or _path_is_relative_to(resolved, ROOT):
            return "area_schedule_artifact_inside_public_repo"
    payload = {
        "schema": "skeleton.aufmass_private_area_schedule.v1",
        "source_pack_id": entry.source_pack_id,
        "run_id": entry.run_id,
        "room_area_row_count": len(room_rows),
        "wall_area_row_count": len(wall_rows),
        "warning_count": warning_count,
        "diagnostic_count": len(diagnostics),
        "room_area_schedule": room_rows,
        "wall_area_schedule": wall_rows,
        "diagnostics": diagnostics,
    }
    try:
        entry.output_root.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        with room_csv_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=(
                    "table_index",
                    "row_index",
                    "room_ref",
                    "floor_area_m2",
                    "ceiling_area_m2",
                    "floor_area_status",
                    "ceiling_area_status",
                    "ceiling_area_confidence",
                ),
            )
            writer.writeheader()
            for row in room_rows:
                evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
                floor = evidence.get("floor_area_m2") if isinstance(evidence, dict) else {}
                ceiling = evidence.get("ceiling_area_m2") if isinstance(evidence, dict) else {}
                writer.writerow(
                    {
                        "table_index": row.get("table_index"),
                        "row_index": row.get("row_index"),
                        "room_ref": row.get("room_ref", ""),
                        "floor_area_m2": row.get("floor_area_m2"),
                        "ceiling_area_m2": row.get("ceiling_area_m2"),
                        "floor_area_status": floor.get("status", "")
                        if isinstance(floor, dict)
                        else "",
                        "ceiling_area_status": ceiling.get("status", "")
                        if isinstance(ceiling, dict)
                        else "",
                        "ceiling_area_confidence": ceiling.get("confidence", "")
                        if isinstance(ceiling, dict)
                        else "",
                    }
                )
        with wall_csv_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=(
                    "table_index",
                    "row_index",
                    "wall_ref",
                    "wall_length_m",
                    "height_m",
                    "gross_wall_area_m2",
                    "opening_area_m2",
                    "opening_area_status",
                    "net_wall_area_m2",
                ),
            )
            writer.writeheader()
            for row in wall_rows:
                writer.writerow(
                    {
                        "table_index": row.get("table_index"),
                        "row_index": row.get("row_index"),
                        "wall_ref": row.get("wall_ref", ""),
                        "wall_length_m": row.get("wall_length_m"),
                        "height_m": row.get("height_m"),
                        "gross_wall_area_m2": row.get("gross_wall_area_m2"),
                        "opening_area_m2": row.get("opening_area_m2"),
                        "opening_area_status": row.get("opening_area_status"),
                        "net_wall_area_m2": row.get("net_wall_area_m2"),
                    }
                )
    except OSError:
        return "area_schedule_artifact_write_failed"
    return None


def build_aufmass_private_area_schedule(body: str) -> str:
    task_id = BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE
    request, report = _aufmass_private_automation_request(task_id, body, include_mode=False)
    if report is not None:
        return report
    assert request is not None

    _checkout_path, workspace_root, reason = _aufmass_private_registered_paths()
    if reason is not None or workspace_root is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"source_pack_id={request.source_pack_id}",
                reason or "reason=private_project_unavailable",
            ],
            "not_met",
        )
    entry, report = _resolve_aufmass_private_registry_entry(task_id, request, workspace_root)
    if report is not None:
        return report
    assert entry is not None

    table_paths = _review_table_private_paths(entry.output_root)
    room_rows, wall_rows, diagnostics, warning_count = _build_private_area_schedules(
        table_paths
    )
    reason = _write_private_area_schedule_artifacts(
        entry, room_rows, wall_rows, diagnostics, warning_count
    )
    if reason is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"source_pack_id={request.source_pack_id}",
                f"run_id={entry.run_id}",
                f"reason={reason}",
            ],
            "not_met",
        )

    status_lines = [
        "status=done",
        f"source_pack_id={request.source_pack_id}",
        f"run_id={entry.run_id}",
        f"room_area_row_count={len(room_rows)}",
        f"wall_area_row_count={len(wall_rows)}",
        f"warning_count={warning_count}",
        f"diagnostic_count={len(diagnostics)}",
    ]
    return _maintenance_report("DONE", task_id, status_lines, "met")


def build_aufmass_private_shortlist(body: str) -> str:
    task_id = BUILD_AUFMASS_PRIVATE_SHORTLIST
    request, report = _aufmass_private_automation_request(task_id, body, include_mode=False)
    if report is not None:
        return report
    assert request is not None

    _checkout_path, workspace_root, reason = _aufmass_private_registered_paths()
    if reason is not None or workspace_root is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [f"source_pack_id={request.source_pack_id}", reason or "reason=private_project_unavailable"],
            "not_met",
        )
    entry, report = _resolve_aufmass_private_registry_entry(task_id, request, workspace_root)
    if report is not None:
        return report
    assert entry is not None

    table_paths = _review_table_private_paths(entry.output_root)
    rows, status_counts, warning_count = _read_private_review_rows(table_paths)
    shortlist_rows = _select_private_shortlist_rows(rows)
    reason = _write_private_shortlist_artifacts(
        entry,
        shortlist_rows,
        len(table_paths),
        len(rows),
        status_counts,
        warning_count,
    )
    if reason is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"source_pack_id={request.source_pack_id}",
                f"run_id={entry.run_id}",
                f"reason={reason}",
            ],
            "not_met",
        )

    status_count_lines = [
        f"status_count_{status}={status_counts[status]}"
        for status in sorted(status_counts)
    ]
    status_lines = [
        "status=done",
        f"source_pack_id={request.source_pack_id}",
        f"run_id={entry.run_id}",
        f"input_table_count={len(table_paths)}",
        f"input_row_count={len(rows)}",
        f"shortlist_row_count={len(shortlist_rows)}",
        *status_count_lines,
        f"warning_count={warning_count}",
    ]
    return _maintenance_report("DONE", task_id, status_lines, "met")


def summarize_aufmass_private_review(body: str) -> str:
    task_id = SUMMARIZE_AUFMASS_PRIVATE_REVIEW
    request, report = _aufmass_private_automation_request(task_id, body, include_mode=False)
    if report is not None:
        return report
    assert request is not None

    _checkout_path, workspace_root, reason = _aufmass_private_registered_paths()
    if reason is not None or workspace_root is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [f"source_pack_id={request.source_pack_id}", reason or "reason=private_project_unavailable"],
            "not_met",
        )
    entry, report = _resolve_aufmass_private_registry_entry(task_id, request, workspace_root)
    if report is not None:
        return report
    assert entry is not None

    table_paths = _review_table_private_paths(entry.output_root)
    row_count = 0
    warning_count = 0
    source_token_count = 0
    status_counts: dict[str, int] = {}
    for table_path in table_paths:
        table_rows, table_status_counts, table_source_tokens = _summarize_review_table(table_path)
        row_count += table_rows
        warning_count += 1 if table_rows == 0 and not table_status_counts else 0
        source_token_count += table_source_tokens
        for status, count in table_status_counts.items():
            status_counts[status] = status_counts.get(status, 0) + count

    status_count_lines = [
        f"status_count_{status}={status_counts[status]}"
        for status in sorted(status_counts)
    ]
    status_lines = [
        "status=done",
        f"source_pack_id={request.source_pack_id}",
        f"run_id={entry.run_id}",
        f"review_table_count={len(table_paths)}",
        f"row_count={row_count}",
        f"source_token_count={source_token_count}",
        f"warning_count={warning_count}",
        *status_count_lines,
    ]
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _non_interactive_sudo(*args: str) -> list[str]:
    return ["sudo", "-n", *args]


def _run_maintenance_command(
    task_id: str,
    step: str,
    command: list[str],
    status_lines: list[str],
    cwd: str | Path | None = None,
) -> str | None:
    code, _output = run_command(command, cwd=cwd)
    if code == 0:
        status_lines.append(f"step={step} status=done")
        return None
    return _maintenance_report(
        "BLOCKED",
        task_id,
        [*status_lines, f"step={step} status=failed exit_code={code}"],
        "not_met",
    )


def _verify_maintenance_command_output(
    task_id: str,
    step: str,
    command: list[str],
    expected_output: str,
    status_lines: list[str],
) -> str | None:
    code, output = run_command(command)
    if code == 0 and output.strip() == expected_output:
        status_lines.append(f"step={step} status=done")
        return None
    return _maintenance_report(
        "BLOCKED",
        task_id,
        [*status_lines, f"step={step} status=failed"],
        "not_met",
    )


def sync_telegram_callback_poller_runtime(workdir: str) -> str:
    task_id = SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME
    status_lines: list[str] = []
    steps = (
        (
            "stop_callback_timer",
            _non_interactive_sudo("systemctl", "stop", TELEGRAM_CALLBACK_POLLER_TIMER),
            None,
        ),
        (
            "stop_callback_service",
            _non_interactive_sudo(
                "systemctl", "stop", TELEGRAM_CALLBACK_POLLER_SERVICE
            ),
            None,
        ),
        ("fetch_origin_main", ["git", "fetch", "origin", "main"], workdir),
        ("checkout_main", ["git", "checkout", "main"], workdir),
        ("pull_origin_main", ["git", "pull", "--ff-only", "origin", "main"], workdir),
    )
    for step, command, cwd in steps:
        report = _run_maintenance_command(task_id, step, command, status_lines, cwd)
        if report is not None:
            return report

    repository = Path(workdir)
    missing_files = [
        relative_path
        for relative_path in TELEGRAM_CALLBACK_POLLER_RUNTIME_FILES
        if not (repository / relative_path).is_file()
    ]
    if missing_files:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                "step=verify_callback_runtime_files status=failed",
                *[f"missing_file={file_name}" for file_name in missing_files],
            ],
            "not_met",
        )
    status_lines.append("step=verify_callback_runtime_files status=done")

    source_service = repository / "scripts" / TELEGRAM_CALLBACK_POLLER_SERVICE
    source_timer = repository / "scripts" / TELEGRAM_CALLBACK_POLLER_TIMER
    installed_service = f"/etc/systemd/system/{TELEGRAM_CALLBACK_POLLER_SERVICE}"
    installed_timer = f"/etc/systemd/system/{TELEGRAM_CALLBACK_POLLER_TIMER}"
    copy_and_start_steps = (
        (
            "copy_callback_service_unit",
            _non_interactive_sudo("cp", str(source_service), installed_service),
        ),
        (
            "copy_callback_timer_unit",
            _non_interactive_sudo("cp", str(source_timer), installed_timer),
        ),
        (
            "own_callback_service_unit",
            _non_interactive_sudo("chown", "root:root", installed_service),
        ),
        (
            "own_callback_timer_unit",
            _non_interactive_sudo("chown", "root:root", installed_timer),
        ),
        (
            "mode_callback_service_unit",
            _non_interactive_sudo("chmod", "0644", installed_service),
        ),
        (
            "mode_callback_timer_unit",
            _non_interactive_sudo("chmod", "0644", installed_timer),
        ),
        ("systemd_daemon_reload", _non_interactive_sudo("systemctl", "daemon-reload")),
        (
            "enable_callback_timer",
            _non_interactive_sudo("systemctl", "enable", TELEGRAM_CALLBACK_POLLER_TIMER),
        ),
        (
            "start_callback_timer",
            _non_interactive_sudo("systemctl", "start", TELEGRAM_CALLBACK_POLLER_TIMER),
        ),
        (
            "run_callback_service_once",
            _non_interactive_sudo(
                "systemctl", "start", TELEGRAM_CALLBACK_POLLER_SERVICE
            ),
        ),
        (
            "verify_callback_timer_active",
            _non_interactive_sudo(
                "systemctl", "is-active", "--quiet", TELEGRAM_CALLBACK_POLLER_TIMER
            ),
        ),
    )
    for step, command in copy_and_start_steps:
        report = _run_maintenance_command(task_id, step, command, status_lines)
        if report is not None:
            return report

    report = _verify_maintenance_command_output(
        task_id,
        "verify_callback_service_result",
        _non_interactive_sudo(
            "systemctl",
            "show",
            "--property=Result",
            "--value",
            TELEGRAM_CALLBACK_POLLER_SERVICE,
        ),
        "success",
        status_lines,
    )
    if report is not None:
        return report

    return _maintenance_report("DONE", task_id, status_lines, "met")


_ENSURE_CALLBACK_HMAC_SCRIPT = """\
from pathlib import Path
import secrets
import sys

path = Path(sys.argv[1])
name = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
prefix = f"{name}="
replacement = f"{prefix}{secrets.token_urlsafe(48)}"
for index, line in enumerate(lines):
    if line.startswith(prefix):
        if line[len(prefix):].strip():
            break
        lines[index] = replacement
        path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
        break
else:
    if lines:
        lines.append(replacement)
    else:
        lines = [replacement]
    path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
"""

_VERIFY_CALLBACK_HMAC_SCRIPT = """\
from pathlib import Path
import sys

prefix = f"{sys.argv[2]}="
lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
raise SystemExit(
    0 if any(line.startswith(prefix) and line[len(prefix):].strip() for line in lines) else 1
)
"""


def ensure_telegram_callback_local_config() -> str:
    task_id = ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG
    status_lines: list[str] = []
    steps = (
        (
            "create_callback_local_config",
            _non_interactive_sudo("touch", TELEGRAM_CALLBACK_LOCAL_CONFIG),
        ),
        (
            "own_callback_local_config",
            _non_interactive_sudo(
                "chown", "root:root", TELEGRAM_CALLBACK_LOCAL_CONFIG
            ),
        ),
        (
            "mode_callback_local_config",
            _non_interactive_sudo("chmod", "0600", TELEGRAM_CALLBACK_LOCAL_CONFIG),
        ),
        (
            "ensure_callback_hmac_secret",
            _non_interactive_sudo(
                "python3",
                "-c",
                _ENSURE_CALLBACK_HMAC_SCRIPT,
                TELEGRAM_CALLBACK_LOCAL_CONFIG,
                TELEGRAM_CALLBACK_HMAC_ENV,
            ),
        ),
        (
            "verify_callback_hmac_secret",
            _non_interactive_sudo(
                "python3",
                "-c",
                _VERIFY_CALLBACK_HMAC_SCRIPT,
                TELEGRAM_CALLBACK_LOCAL_CONFIG,
                TELEGRAM_CALLBACK_HMAC_ENV,
            ),
        ),
    )
    for step, command in steps:
        report = _run_maintenance_command(task_id, step, command, status_lines)
        if report is not None:
            return report
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _target_project_metadata_field(body: str) -> str | None:
    metadata = (body or "").split("```task", 1)[0]
    return _body_field(metadata, "Target Project")


def _project_checkout_path_is_under_runner_base(checkout_path: Path) -> bool:
    try:
        checkout_path.resolve(strict=False).relative_to(RUNNER_PROJECT_CHECKOUT_BASE)
    except ValueError:
        return False
    return True


def _remote_url_matches_project_repo(remote_url: str, repo: str) -> bool:
    remote_url = remote_url.strip()
    expected = repo.strip().removesuffix(".git")
    candidates = {
        expected,
        f"https://github.com/{expected}",
        f"git@github.com:{expected}",
        f"ssh://git@github.com/{expected}",
    }
    return remote_url.removesuffix(".git") in candidates


def _registered_checkout_public_status_lines(
    target_project: str, target_repository: str
) -> list[str]:
    return [
        f"target_project={target_project}",
        f"target_repository={target_repository}",
        "target_project_route=registered_checkout",
    ]


def _registered_project_checkout(
    task_id: str, body: str
) -> tuple[RegisteredProjectCheckout | None, str | None]:
    target_project = _target_project_metadata_field(body)
    if target_project is None:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=missing_target_project"],
            "not_met",
        )

    projects = load_runner_project_tree().get("projects")
    project = projects.get(target_project) if isinstance(projects, dict) else None
    if not isinstance(project, dict):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [
                f"target_project={target_project}",
                "reason=target_project_unknown",
            ],
            "not_met",
        )

    checkout_path_text = str(project["checkout_path"])
    checkout_path = Path(checkout_path_text)
    repo = str(project["repo"])
    status_lines = _registered_checkout_public_status_lines(
        target_project, repo
    )
    if any(part == ".." for part in checkout_path.parts):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_traversal"],
            "not_met",
        )
    if not _project_checkout_path_is_under_runner_base(checkout_path):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_unsafe"],
            "not_met",
        )
    return RegisteredProjectCheckout(
        target_project=target_project,
        repo=repo,
        checkout_path_text=checkout_path_text,
        checkout_path=checkout_path,
        status_lines=status_lines,
    ), None


def _verify_registered_project_checkout(
    task_id: str, registered_checkout: RegisteredProjectCheckout
) -> str:
    checkout_path = registered_checkout.checkout_path
    status_lines = registered_checkout.status_lines
    if not checkout_path.exists():
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_missing"],
            "not_met",
        )
    if not (checkout_path / ".git").exists():
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_git_missing"],
            "not_met",
        )

    command = ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]
    code, output = run_command(command)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_origin_remote status=failed"],
            "not_met",
        )
    if not _remote_url_matches_project_repo(output, registered_checkout.repo):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_origin_remote status=failed"],
            "not_met",
        )

    return _maintenance_report(
        "DONE",
        task_id,
        [
            *status_lines,
            "step=read_origin_remote status=done",
            "step=verify_origin_remote status=done",
        ],
        "met",
    )


def check_project_checkout(body: str) -> str:
    task_id = CHECK_PROJECT_CHECKOUT
    registered_checkout, report = _registered_project_checkout(task_id, body)
    if report is not None:
        return report
    assert registered_checkout is not None
    return _verify_registered_project_checkout(task_id, registered_checkout)


def _registered_skeleton_checkout(
    task_id: str,
) -> tuple[RegisteredProjectCheckout | None, str | None]:
    projects = load_runner_project_tree().get("projects")
    project_items = projects.items() if isinstance(projects, dict) else ()
    skeleton_projects = [
        (project_id, project)
        for project_id, project in project_items
        if isinstance(project, dict) and project.get("repo") == REPO
    ]
    if len(skeleton_projects) != 1:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            ["target_repository=alanua/Skeleton", "reason=skeleton_checkout_unknown"],
            "not_met",
        )

    target_project, project = skeleton_projects[0]
    checkout_path_text = str(project["checkout_path"])
    checkout_path = Path(checkout_path_text)
    repo = str(project["repo"])
    status_lines = _registered_checkout_public_status_lines(target_project, repo)
    if any(part == ".." for part in checkout_path.parts):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_traversal"],
            "not_met",
        )
    if not _project_checkout_path_is_under_runner_base(checkout_path):
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_unsafe"],
            "not_met",
        )
    return RegisteredProjectCheckout(
        target_project=target_project,
        repo=repo,
        checkout_path_text=checkout_path_text,
        checkout_path=checkout_path,
        status_lines=status_lines,
    ), None


def _run_freshness_command(
    command: list[str], status_lines: list[str], step: str
) -> tuple[str | None, str | None]:
    code, output = run_command(command)
    if code == 0:
        status_lines.append(f"step={step} status=done")
        return output.strip(), None
    return None, f"step={step} status=failed exit_code={code}"


def _skeleton_expected_head_sha(body: str) -> tuple[str | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    expected_head_sha = _body_field(metadata, "Expected Head SHA")
    if expected_head_sha is None:
        return None, None
    if _HEAD_SHA_RE.fullmatch(expected_head_sha) is None:
        return None, "invalid_expected_head_sha"
    return expected_head_sha.lower(), None


def _skeleton_checkout_recovery_root() -> Path:
    configured = os.environ.get("SKELETON_RUNNER_CHECKOUT_RECOVERY_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / "skeleton" / "checkout-recovery"


def _path_has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor) if path.is_absolute() else Path()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _ensure_private_skeleton_checkout_recovery_root() -> Path:
    root = _skeleton_checkout_recovery_root()
    if not root.is_absolute():
        raise ValueError("recovery root must be absolute")
    if ".." in root.parts or _path_has_symlink_component(root):
        raise ValueError("recovery root is unsafe")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root_stat = root.stat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("recovery root is not a directory")
    if root_stat.st_uid != os.getuid():
        raise ValueError("recovery root is not owned by the current user")
    if stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise ValueError("recovery root permissions are unsafe")
    return root


def _restore_skeleton_checkout_stash(checkout_path: Path) -> str:
    code, _output = run_command(
        ["git", "-C", str(checkout_path), "stash", "apply", "stash@{0}"]
    )
    if code != 0:
        return "failed"
    code, _output = run_command(
        ["git", "-C", str(checkout_path), "stash", "drop", "stash@{0}"]
    )
    if code != 0:
        return "failed"
    return "restored"


def _create_verified_skeleton_checkout_recovery(
    checkout_path: Path, status_lines: list[str]
) -> tuple[str | None, str | None]:
    code, _output = run_command(
        [
            "git",
            "-C",
            str(checkout_path),
            "stash",
            "push",
            "--include-untracked",
            "-m",
            "skeleton-runner-checkout-recovery",
        ]
    )
    if code != 0:
        return None, "recovery_stash_failed"
    status_lines.append("recovery_stash_status=created")

    code, output = run_command(
        ["git", "-C", str(checkout_path), "rev-parse", "refs/stash"]
    )
    stash_sha = (
        output.strip().splitlines()[0].lower()
        if code == 0 and output.strip()
        else ""
    )
    if code != 0 or _HEAD_SHA_RE.fullmatch(stash_sha) is None:
        restore_status = _restore_skeleton_checkout_stash(checkout_path)
        status_lines.append(f"recovery_restore_status={restore_status}")
        return None, "recovery_stash_sha_invalid"

    try:
        recovery_root = _ensure_private_skeleton_checkout_recovery_root()
    except (OSError, ValueError):
        restore_status = _restore_skeleton_checkout_stash(checkout_path)
        status_lines.append(f"recovery_restore_status={restore_status}")
        return None, "recovery_root_unavailable"

    recovery_ref = f"refs/skeleton-runner/pending-recovery/{stash_sha}"
    code, _output = run_command(
        ["git", "-C", str(checkout_path), "update-ref", recovery_ref, stash_sha]
    )
    if code != 0:
        restore_status = _restore_skeleton_checkout_stash(checkout_path)
        status_lines.append(f"recovery_restore_status={restore_status}")
        return None, "recovery_ref_failed"
    status_lines.append("recovery_ref_status=created")

    bundle_path = recovery_root / f"skeleton-checkout-{stash_sha}.bundle"
    try:
        bundle_path.relative_to(recovery_root)
    except ValueError:
        restore_status = _restore_skeleton_checkout_stash(checkout_path)
        status_lines.append(f"recovery_restore_status={restore_status}")
        return stash_sha, "recovery_artifact_path_unsafe"

    code, _output = run_command(
        [
            "git",
            "-C",
            str(checkout_path),
            "bundle",
            "create",
            str(bundle_path),
            recovery_ref,
        ]
    )
    if code != 0:
        restore_status = _restore_skeleton_checkout_stash(checkout_path)
        status_lines.append(f"recovery_restore_status={restore_status}")
        run_command(["git", "-C", str(checkout_path), "update-ref", "-d", recovery_ref])
        try:
            bundle_path.unlink(missing_ok=True)
        except OSError:
            pass
        return stash_sha, "recovery_artifact_create_failed"

    code, _output = run_command(
        ["git", "-C", str(checkout_path), "bundle", "verify", str(bundle_path)]
    )
    if code != 0:
        restore_status = _restore_skeleton_checkout_stash(checkout_path)
        status_lines.append(f"recovery_restore_status={restore_status}")
        run_command(["git", "-C", str(checkout_path), "update-ref", "-d", recovery_ref])
        try:
            bundle_path.unlink(missing_ok=True)
        except OSError:
            pass
        return stash_sha, "recovery_artifact_verify_failed"

    status_lines.append("recovery_artifact_status=verified")
    return stash_sha, None


def _verify_skeleton_checkout_present(
    task_id: str, registered_checkout: RegisteredProjectCheckout
) -> str | None:
    checkout_path = registered_checkout.checkout_path
    status_lines = registered_checkout.status_lines
    if not checkout_path.exists():
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_path_missing"],
            "not_met",
        )
    if not (checkout_path / ".git").exists():
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_git_missing"],
            "not_met",
        )
    return None


def _read_skeleton_origin(
    task_id: str,
    registered_checkout: RegisteredProjectCheckout,
    status_lines: list[str],
) -> str | None:
    checkout_path = registered_checkout.checkout_path
    origin_url, failure = _run_freshness_command(
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
        status_lines,
        "read_origin_remote",
    )
    if failure is not None or origin_url is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "reason=origin_read_failed"],
            "not_met",
        )
    if not _remote_url_matches_project_repo(origin_url, registered_checkout.repo):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_origin_remote status=failed"],
            "not_met",
        )
    status_lines.append("step=verify_origin_remote status=done")
    return None


def _read_skeleton_clean_state(
    task_id: str, checkout_path: Path, status_lines: list[str]
) -> str | None:
    status_output, failure = _run_freshness_command(
        ["git", "-C", str(checkout_path), "status", "--porcelain"],
        status_lines,
        "read_worktree_status",
    )
    if failure is not None or status_output is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "reason=worktree_status_read_failed"],
            "not_met",
        )
    if status_output.strip():
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_dirty"],
            "not_met",
        )
    return None


def _read_skeleton_current_branch(
    task_id: str, checkout_path: Path, status_lines: list[str]
) -> str | None:
    current_branch, failure = _run_freshness_command(
        ["git", "-C", str(checkout_path), "symbolic-ref", "--short", "HEAD"],
        status_lines,
        "read_current_branch",
    )
    if failure is not None or current_branch is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "step=read_current_branch status=failed", "reason=detached_head"],
            "not_met",
        )
    if current_branch != "main":
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"current_branch={current_branch}", "reason=branch_not_main"],
            "not_met",
        )
    status_lines.append("current_branch=main")
    return None


def _fetch_skeleton_origin_main(
    task_id: str, checkout_path: Path, status_lines: list[str]
) -> str | None:
    _output, failure = _run_freshness_command(
        ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"],
        status_lines,
        "fetch_origin_main",
    )
    if failure is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure],
            "not_met",
        )
    return None


def _read_skeleton_sha(
    task_id: str, checkout_path: Path, ref: str, status_lines: list[str], step: str
) -> tuple[str | None, str | None]:
    sha, failure = _run_freshness_command(
        ["git", "-C", str(checkout_path), "rev-parse", ref],
        status_lines,
        step,
    )
    if failure is not None or sha is None or _HEAD_SHA_RE.fullmatch(sha) is None:
        return None, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or f"reason={step}_failed"],
            "not_met",
        )
    return sha.lower(), None


def _count_bounded_cli_rows(output: str) -> int:
    return len([line for line in (output or "").splitlines() if line.strip()])


def _classify_skeleton_checkout_sync(
    checkout_path: Path, head_sha: str, github_main_sha: str
) -> tuple[str | None, str | None]:
    if head_sha == github_main_sha:
        return "equal", None

    code, _output = run_command(
        [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            head_sha,
            github_main_sha,
        ]
    )
    if code == 0:
        return "behind", None
    if code != 1:
        return None, f"step=classify_checkout_behind status=failed exit_code={code}"

    code, _output = run_command(
        [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            github_main_sha,
            head_sha,
        ]
    )
    if code == 0:
        return "ahead", None
    if code == 1:
        return "diverged", None
    return None, f"step=classify_checkout_ahead status=failed exit_code={code}"


def check_skeleton_freshness() -> str:
    task_id = CHECK_SKELETON_FRESHNESS
    registered_checkout, report = _registered_skeleton_checkout(task_id)
    if report is not None:
        return report
    assert registered_checkout is not None

    checkout_path = registered_checkout.checkout_path
    status_lines = list(registered_checkout.status_lines)
    present_report = _verify_skeleton_checkout_present(task_id, registered_checkout)
    if present_report is not None:
        return present_report

    origin_report = _read_skeleton_origin(task_id, registered_checkout, status_lines)
    if origin_report is not None:
        return origin_report

    clean_report = _read_skeleton_clean_state(task_id, checkout_path, status_lines)
    if clean_report is not None:
        return clean_report

    fetch_report = _fetch_skeleton_origin_main(task_id, checkout_path, status_lines)
    if fetch_report is not None:
        return fetch_report

    head_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "HEAD", status_lines, "read_checkout_head"
    )
    if failure_report is not None or head_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=checkout_head_read_failed"], "not_met"
        )

    origin_main_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "origin/main", status_lines, "read_origin_main"
    )
    if failure_report is not None or origin_main_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=origin_main_read_failed"],
            "not_met",
        )

    github_main_output, failure = _run_freshness_command(
        ["git", "-C", str(checkout_path), "ls-remote", "origin", "refs/heads/main"],
        status_lines,
        "read_github_main",
    )
    github_main_parts = (github_main_output or "").split()
    github_main_sha = github_main_parts[0] if github_main_parts else ""
    if (
        failure is not None
        or _HEAD_SHA_RE.fullmatch(github_main_sha) is None
        or len(github_main_parts) < 2
        or github_main_parts[1] != "refs/heads/main"
    ):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "reason=github_main_read_failed"],
            "not_met",
        )

    if origin_main_sha != github_main_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=origin_main_not_current_github_main"],
            "not_met",
        )

    sync_state, failure = _classify_skeleton_checkout_sync(
        checkout_path, head_sha, github_main_sha
    )
    if failure is not None or sync_state is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "reason=unclassified_sync_state"],
            "not_met",
        )
    if sync_state not in {"equal", "ahead", "behind", "diverged"}:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=unclassified_sync_state"],
            "not_met",
        )
    if sync_state in {"behind", "diverged"}:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"checkout_head_sha={head_sha}",
                f"github_main_sha={github_main_sha}",
                f"checkout_sync_state={sync_state}",
                f"reason=checkout_{sync_state}",
            ],
            "not_met",
        )

    open_prs_output, failure = _run_freshness_command(
        ["gh", "pr", "list", "--repo", REPO, "--state", "open"],
        status_lines,
        "query_open_pull_requests",
    )
    if failure is not None or open_prs_output is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "reason=github_query_failed"],
            "not_met",
        )

    open_issues_output, failure = _run_freshness_command(
        ["gh", "issue", "list", "--repo", REPO, "--state", "open"],
        status_lines,
        "query_open_issues",
    )
    if failure is not None or open_issues_output is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "reason=github_query_failed"],
            "not_met",
        )

    status_lines.extend(
        (
            f"checkout_head_sha={head_sha}",
            f"github_main_sha={github_main_sha}",
            "github_main_source_of_truth=true",
            f"checkout_sync_state={sync_state}",
            f"open_pull_requests_count={_count_bounded_cli_rows(open_prs_output)}",
            f"open_issues_count={_count_bounded_cli_rows(open_issues_output)}",
            "sourcepack_note=refresh_docs/NOTEBOOKLM_SOURCEPACK.md_when_sourcepack_or_notebooklm_context_changes",
            "review_note=open_prs_and_issues_may_need_rebase_retest_or_scope_review_against_current_main",
            "canon_note=old_chats_and_old_branches_are_not_canon",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def runtime_sync_main(body: str) -> str:
    task_id = RUNTIME_SYNC_MAIN
    expected_head_sha, reason = _skeleton_expected_head_sha(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")

    registered_checkout, report = _registered_skeleton_checkout(task_id)
    if report is not None:
        return report
    assert registered_checkout is not None

    checkout_path = registered_checkout.checkout_path
    status_lines = list(registered_checkout.status_lines)
    if expected_head_sha is not None:
        status_lines.append(f"expected_head_sha={expected_head_sha}")

    present_report = _verify_skeleton_checkout_present(task_id, registered_checkout)
    if present_report is not None:
        return present_report

    origin_report = _read_skeleton_origin(task_id, registered_checkout, status_lines)
    if origin_report is not None:
        return origin_report

    branch_report = _read_skeleton_current_branch(task_id, checkout_path, status_lines)
    if branch_report is not None:
        return branch_report

    clean_report = _read_skeleton_clean_state(task_id, checkout_path, status_lines)
    if clean_report is not None:
        return clean_report

    fetch_report = _fetch_skeleton_origin_main(task_id, checkout_path, status_lines)
    if fetch_report is not None:
        return fetch_report

    head_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "HEAD", status_lines, "read_checkout_head"
    )
    if failure_report is not None or head_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=checkout_head_read_failed"], "not_met"
        )

    origin_main_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "origin/main", status_lines, "read_origin_main"
    )
    if failure_report is not None or origin_main_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=origin_main_read_failed"], "not_met"
        )
    if expected_head_sha is not None and origin_main_sha != expected_head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"github_main_sha={origin_main_sha}",
                "reason=expected_head_sha_mismatch",
            ],
            "not_met",
        )

    sync_state, failure = _classify_skeleton_checkout_sync(
        checkout_path, head_sha, origin_main_sha
    )
    if failure is not None or sync_state is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "reason=unclassified_sync_state"],
            "not_met",
        )
    if sync_state in {"ahead", "diverged"}:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"checkout_head_sha={head_sha}",
                f"github_main_sha={origin_main_sha}",
                f"checkout_sync_state={sync_state}",
                f"reason=checkout_{sync_state}",
            ],
            "not_met",
        )
    if sync_state == "behind":
        code, _output = run_command(
            ["git", "-C", str(checkout_path), "merge", "--ff-only", "origin/main"]
        )
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [*status_lines, f"step=fast_forward_main status=failed exit_code={code}"],
                "not_met",
            )
        status_lines.append("step=fast_forward_main status=done")
    elif sync_state == "equal":
        status_lines.append("step=fast_forward_main status=not_needed")
    else:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=unclassified_sync_state"],
            "not_met",
        )

    final_head_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "HEAD", status_lines, "read_final_head"
    )
    if failure_report is not None or final_head_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=final_head_read_failed"], "not_met"
        )
    if final_head_sha != origin_main_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"checkout_head_sha={final_head_sha}",
                f"github_main_sha={origin_main_sha}",
                "reason=final_head_mismatch",
            ],
            "not_met",
        )
    if expected_head_sha is not None and final_head_sha != expected_head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"checkout_head_sha={final_head_sha}",
                "reason=expected_head_sha_mismatch",
            ],
            "not_met",
        )

    status_lines.extend(
        (
            f"checkout_head_sha={final_head_sha}",
            f"github_main_sha={origin_main_sha}",
            "checkout_sync_state=equal",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def recover_skeleton_checkout(body: str) -> str:
    task_id = RECOVER_SKELETON_CHECKOUT
    expected_head_sha, reason = _skeleton_expected_head_sha(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")

    registered_checkout, report = _registered_skeleton_checkout(task_id)
    if report is not None:
        return report
    assert registered_checkout is not None

    checkout_path = registered_checkout.checkout_path
    status_lines = list(registered_checkout.status_lines)
    if expected_head_sha is not None:
        status_lines.append(f"expected_head_sha={expected_head_sha}")

    present_report = _verify_skeleton_checkout_present(task_id, registered_checkout)
    if present_report is not None:
        return present_report

    origin_report = _read_skeleton_origin(task_id, registered_checkout, status_lines)
    if origin_report is not None:
        return origin_report

    branch_report = _read_skeleton_current_branch(task_id, checkout_path, status_lines)
    if branch_report is not None:
        return branch_report

    status_output, failure = _run_freshness_command(
        ["git", "-C", str(checkout_path), "status", "--porcelain"],
        status_lines,
        "read_worktree_status",
    )
    if failure is not None or status_output is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, failure or "reason=worktree_status_read_failed"],
            "not_met",
        )

    fetch_report = _fetch_skeleton_origin_main(task_id, checkout_path, status_lines)
    if fetch_report is not None:
        return fetch_report

    origin_main_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "origin/main", status_lines, "read_origin_main"
    )
    if failure_report is not None or origin_main_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=origin_main_read_failed"], "not_met"
        )
    if expected_head_sha is not None and origin_main_sha != expected_head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"github_main_sha={origin_main_sha}",
                "reason=expected_head_sha_mismatch",
            ],
            "not_met",
        )

    if status_output.strip():
        _stash_sha, recovery_reason = _create_verified_skeleton_checkout_recovery(
            checkout_path, status_lines
        )
        if recovery_reason is not None:
            return _maintenance_report(
                "NEEDS_OPERATOR",
                task_id,
                [*status_lines, f"reason={recovery_reason}"],
                "not_met",
            )
    else:
        status_lines.append("recovery_stash_status=not_needed")

    code, _output = run_command(
        ["git", "-C", str(checkout_path), "reset", "--hard", "origin/main"]
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reset_status=failed", f"exit_code={code}"],
            "not_met",
        )
    status_lines.append("reset_status=hard_origin_main")

    code, _output = run_command(["git", "-C", str(checkout_path), "clean", "-fd"])
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"step=clean_untracked status=failed exit_code={code}"],
            "not_met",
        )
    status_lines.append("step=clean_untracked status=done")

    final_status_output, final_status_failure = _run_freshness_command(
        [
            "git",
            "-C",
            str(checkout_path),
            "status",
            "--porcelain",
            "--untracked-files=all",
        ],
        status_lines,
        "read_final_worktree_status",
    )
    if final_status_failure is not None or final_status_output is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                "final_clean_state=false",
                final_status_failure or "reason=final_worktree_status_read_failed",
            ],
            "not_met",
        )
    if final_status_output.strip():
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                "final_clean_state=false",
                "reason=final_checkout_dirty",
            ],
            "not_met",
        )
    status_lines.append("final_clean_state=true")

    final_head_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "HEAD", status_lines, "read_final_head"
    )
    if failure_report is not None or final_head_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=final_head_read_failed"],
            "not_met",
        )
    if final_head_sha != origin_main_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"checkout_head_sha={final_head_sha}",
                f"github_main_sha={origin_main_sha}",
                "reason=final_head_mismatch",
            ],
            "not_met",
        )

    status_lines.extend(
        (
            f"checkout_head_sha={final_head_sha}",
            f"github_main_sha={origin_main_sha}",
            "checkout_sync_state=equal",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def ensure_project_checkout(body: str) -> str:
    task_id = ENSURE_PROJECT_CHECKOUT
    registered_checkout, report = _registered_project_checkout(task_id, body)
    if report is not None:
        return report
    assert registered_checkout is not None

    checkout_path = registered_checkout.checkout_path
    status_lines = registered_checkout.status_lines
    if checkout_path.exists():
        return _verify_registered_project_checkout(task_id, registered_checkout)

    try:
        checkout_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                "step=prepare_checkout_parent status=failed",
                "reason=checkout_parent_prepare_failed",
            ],
            "not_met",
        )
    status_lines.append("step=prepare_checkout_parent status=done")

    clone_url = f"https://github.com/{registered_checkout.repo.removesuffix('.git')}.git"
    code, _output = run_command(["git", "clone", clone_url, str(checkout_path)])
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"step=prepare_checkout status=failed exit_code={code}"],
            "not_met",
        )
    status_lines.append("step=prepare_checkout status=done")
    return _verify_registered_project_checkout(task_id, registered_checkout)


def _pr_branch_validation_metadata(
    body: str,
) -> tuple[PrBranchValidationRequest | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    repository, _repository_field = _target_repository_metadata_field(metadata)
    repository = repository or _body_field(metadata, "Repository") or REPO
    pr_number = _body_field(metadata, "Pull Request")
    expected_head_sha = _body_field(metadata, "Expected Head SHA")
    expected_base_sha = _body_field(metadata, "Expected Base SHA")
    profile = _body_field(metadata, "Validation Profile") or "full_pytest"
    if repository not in ALLOWED_TARGET_REPOSITORIES:
        return None, "unsupported_repository"
    if not isinstance(pr_number, str) or not re.fullmatch(r"[1-9]\d*", pr_number):
        return None, "missing_or_invalid_pull_request"
    if (
        expected_head_sha is not None
        and _HEAD_SHA_RE.fullmatch(expected_head_sha) is None
    ):
        return None, "invalid_expected_head_sha"
    if expected_base_sha is None:
        return None, "missing_expected_base_sha"
    if _HEAD_SHA_RE.fullmatch(expected_base_sha) is None:
        return None, "invalid_expected_base_sha"
    if profile not in PR_BRANCH_VALIDATION_PROFILES:
        return None, "unsupported_validation_profile"
    return (
        PrBranchValidationRequest(
            repository=repository,
            pr_number=int(pr_number),
            expected_head_sha=(
                expected_head_sha.lower()
                if isinstance(expected_head_sha, str)
                else None
            ),
            expected_base_sha=expected_base_sha.lower(),
            profile=profile,
        ),
        None,
    )


def _get_pr_branch_validation_state(repository: str, pr_number: int) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repository,
            "--json",
            "number,state,baseRefName,baseRefOid,headRefName,headRefOid",
        ]
    )
    if code != 0:
        raise RuntimeError("gh pr view failed")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh pr view returned non-object JSON")
    return parsed


def _preflight_pr_refresh_metadata(
    body: str,
) -> tuple[PreflightPrRefreshRequest | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    pr_number = _body_field(metadata, "Pull Request")
    expected_head_sha = _body_field(metadata, "Expected Head SHA")
    if not isinstance(pr_number, str) or not re.fullmatch(r"[1-9]\d*", pr_number):
        return None, "missing_or_invalid_pull_request"
    if (
        expected_head_sha is not None
        and _HEAD_SHA_RE.fullmatch(expected_head_sha) is None
    ):
        return None, "invalid_expected_head_sha"
    return (
        PreflightPrRefreshRequest(
            pr_number=int(pr_number),
            expected_head_sha=(
                expected_head_sha.lower()
                if isinstance(expected_head_sha, str)
                else None
            ),
        ),
        None,
    )


def _get_preflight_pr_refresh_state(pr_number: int) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            REPO,
            "--json",
            (
                "number,state,baseRefName,headRefName,headRefOid,"
                "headRepository,headRepositoryOwner,files"
            ),
        ]
    )
    if code != 0:
        raise RuntimeError("gh pr view failed")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh pr view returned non-object JSON")
    return parsed


def _get_preflight_compare_state(head_sha: str) -> dict[str, Any]:
    code, output = run_command(
        ["gh", "api", f"repos/{REPO}/compare/main...{head_sha}"]
    )
    if code != 0:
        raise RuntimeError("gh compare failed")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh compare returned non-object JSON")
    return parsed


def _main_contains_path(path: str) -> bool:
    code, _output = run_command(
        [
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/{REPO}/contents/{urllib.parse.quote(path, safe='/')}",
            "-f",
            "ref=main",
        ]
    )
    return code == 0


def _pr_file_paths(pr_state: dict[str, Any]) -> list[str]:
    files = pr_state.get("files")
    if not isinstance(files, list):
        return []
    paths: list[str] = []
    for file_info in files:
        if isinstance(file_info, dict) and isinstance(file_info.get("path"), str):
            paths.append(file_info["path"])
    return paths


def _repository_name_with_owner(repository: object) -> str | None:
    if isinstance(repository, dict):
        name = repository.get("nameWithOwner")
        if isinstance(name, str):
            return name
        owner = repository.get("owner")
        repo_name = repository.get("name")
        if isinstance(owner, dict):
            owner = owner.get("login")
        if isinstance(owner, str) and isinstance(repo_name, str):
            return f"{owner}/{repo_name}"
    return None


def _head_repository_name_with_owner(pr_state: dict[str, Any]) -> str | None:
    repo = _repository_name_with_owner(pr_state.get("headRepository"))
    if repo is not None:
        return repo
    owner = pr_state.get("headRepositoryOwner")
    if isinstance(owner, dict):
        owner = owner.get("login")
    if isinstance(owner, str):
        head_ref = pr_state.get("headRefName")
        if isinstance(head_ref, str) and head_ref:
            return f"{owner}/{REPO.rsplit('/', 1)[1]}"
    return None


def _preflight_refresh_next_action(
    *,
    pr_state: dict[str, Any],
    compare_state: dict[str, Any] | None,
    changed_files: list[str],
    files_on_main: list[str],
) -> str:
    if str(pr_state.get("state") or "").upper() != "OPEN":
        if compare_state is not None and int(compare_state.get("ahead_by") or 0) == 0:
            return "mark_obsolete"
        return "manual_review_required"
    if pr_state.get("baseRefName") != "main":
        return "manual_review_required"
    if (
        not pr_state.get("headRefName")
        or _head_repository_name_with_owner(pr_state) != REPO
    ):
        return "manual_review_required"
    if compare_state is None:
        return "manual_review_required"

    ahead_by = int(compare_state.get("ahead_by") or 0)
    behind_by = int(compare_state.get("behind_by") or 0)
    compare_status = str(compare_state.get("status") or "")
    if ahead_by == 0 or compare_status == "identical":
        return "mark_obsolete"
    if files_on_main:
        return "manual_review_required"
    if changed_files and behind_by > 0:
        return "create_fresh_pr"
    return "validate_and_merge"


def preflight_pr_refresh(body: str) -> str:
    task_id = PREFLIGHT_PR_REFRESH
    request, reason = _preflight_pr_refresh_metadata(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")
    assert request is not None

    status_lines = [
        f"repository={REPO}",
        f"pull_request={request.pr_number}",
    ]
    try:
        pr_state = _get_preflight_pr_refresh_state(request.pr_number)
    except Exception:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_pr_metadata status=failed"],
            "not_met",
        )
    status_lines.append("step=read_pr_metadata status=done")

    if pr_state.get("number") != request.pr_number:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=pr_number_mismatch"],
            "not_met",
        )

    head_sha = str(pr_state.get("headRefOid") or "").lower()
    if _HEAD_SHA_RE.fullmatch(head_sha) is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=pr_head_sha_invalid"],
            "not_met",
        )
    if request.expected_head_sha is not None and head_sha != request.expected_head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=expected_head_sha_mismatch"],
            "not_met",
        )
    status_lines.append(f"head_sha={head_sha}")

    changed_files = _pr_file_paths(pr_state)
    status_lines.append(f"changed_files_count={len(changed_files)}")
    status_lines.extend(f"changed_file={path}" for path in changed_files)

    compare_state: dict[str, Any] | None = None
    try:
        compare_state = _get_preflight_compare_state(head_sha)
    except Exception:
        status_lines.append("step=compare_main_to_head status=failed")
    else:
        status_lines.extend(
            (
                "step=compare_main_to_head status=done",
                f"compare_status={compare_state.get('status')}",
                f"compare_ahead_by={int(compare_state.get('ahead_by') or 0)}",
                f"compare_behind_by={int(compare_state.get('behind_by') or 0)}",
            )
        )

    files_on_main = [path for path in changed_files if _main_contains_path(path)]
    status_lines.append(f"files_on_main_count={len(files_on_main)}")
    status_lines.extend(f"file_on_main={path}" for path in files_on_main)

    next_action = _preflight_refresh_next_action(
        pr_state=pr_state,
        compare_state=compare_state,
        changed_files=changed_files,
        files_on_main=files_on_main,
    )
    status_lines.extend(
        (
            f"pr_state={str(pr_state.get('state') or '').upper()}",
            f"base_ref={pr_state.get('baseRefName')}",
            f"head_ref={pr_state.get('headRefName') or ''}",
            f"head_repository={_head_repository_name_with_owner(pr_state) or ''}",
            f"next_action={next_action}",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _validation_worktree_path(repository: str, pr_number: int) -> Path:
    return (
        target_repository_worktree_root(repository)
        / PR_BRANCH_VALIDATION_WORKTREE_DIR
        / f"pr-{pr_number}"
    )


def _ensure_safe_validation_worktree_path(repository: str, path: str | Path) -> Path:
    root = target_repository_worktree_root(repository).resolve(strict=False)
    candidate = Path(path).expanduser().resolve(strict=False)
    if candidate == root:
        raise ValueError("validation worktree cannot be the worktree root")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("validation worktree is outside runner worktree root") from exc
    return candidate


def _pr_branch_validation_checkout_path(
    repository: str,
) -> tuple[Path | None, str | None]:
    if repository == REPO:
        return ROOT, None
    checkout_block_reason = verify_target_repository_checkout(repository)
    if checkout_block_reason is not None:
        return None, "target_repository_checkout_unavailable"
    return target_repository_checkout_path(repository), None


def _pr_branch_validation_block_reason(
    request: PrBranchValidationRequest, pr_state: dict[str, Any]
) -> str | None:
    if pr_state.get("number") != request.pr_number:
        return "pr_number_mismatch"
    if str(pr_state.get("state") or "").upper() != "OPEN":
        return "pr_not_open"
    if pr_state.get("baseRefName") != "main":
        return "pr_base_not_main"
    base_sha = str(pr_state.get("baseRefOid") or "").lower()
    if _HEAD_SHA_RE.fullmatch(base_sha) is None:
        return "pr_base_sha_invalid"
    if base_sha != request.expected_base_sha:
        return "expected_base_sha_mismatch"
    head_sha = str(pr_state.get("headRefOid") or "").lower()
    if _HEAD_SHA_RE.fullmatch(head_sha) is None:
        return "pr_head_sha_invalid"
    if request.expected_head_sha is not None and head_sha != request.expected_head_sha:
        return "expected_head_sha_mismatch"
    return None


def _sanitize_validation_command_output(output: str) -> str:
    sanitized = _ANSI_ESCAPE_RE.sub("", output or "")
    sanitized = sanitized.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = re.sub(r"https?://[^\s]+", "[redacted_url]", sanitized)
    sanitized = re.sub(r"(?<![\w./-])/(?:[^\s:/]+/)+[^\s:]+", "[redacted_path]", sanitized)
    sanitized = "".join(
        character
        if character == "\n" or character == "\t" or 32 <= ord(character) < 127
        else "?"
        for character in sanitized
    )
    safe_lines: list[str] = []
    for line in sanitized.split("\n"):
        if _ENV_ASSIGNMENT_LINE_RE.fullmatch(line):
            safe_lines.append("[redacted environment variable]")
            continue
        safe_lines.append(
            _SENSITIVE_OUTPUT_VALUE_RE.sub(r"\1=[redacted]", line).rstrip()
        )
    return "\n".join(safe_lines).strip()


def _validation_receipt_value(value: str, *, limit: int = 900) -> str:
    sanitized = _sanitize_validation_command_output(value)
    if not sanitized:
        return "none"
    sanitized = sanitized[-limit:]
    sanitized = sanitized.replace("[redacted environment variable]", "redacted_env")
    sanitized = sanitized.replace("[redacted_path]", "redacted_path")
    sanitized = sanitized.replace("[redacted_url]", "redacted_url")
    sanitized = re.sub(r"\s+", "_", sanitized.strip())
    sanitized = re.sub(r"[^A-Za-z0-9._:+,@/\[\]{}()#-]+", "_", sanitized).strip("_")
    while ".." in sanitized:
        sanitized = sanitized.replace("..", ".dot.")
    return sanitized[:limit] or "none"


def _validation_command_text(command: tuple[str, ...]) -> str:
    return _validation_receipt_value(shlex.join(command), limit=300)


def _bounded_validation_command_output(output: str) -> str:
    sanitized = _sanitize_validation_command_output(output)
    if not sanitized:
        return "(no output)"
    tail = "\n".join(sanitized.splitlines()[-80:])
    if len(tail) <= VALIDATION_FAILED_OUTPUT_LIMIT:
        return tail
    marker = f"{VALIDATION_FAILED_OUTPUT_TRUNCATED_MARKER}\n"
    return marker + tail[-(VALIDATION_FAILED_OUTPUT_LIMIT - len(marker)) :].lstrip()


_PYTEST_TOTALS_RE = re.compile(
    r"(?P<totals>\d+\s+"
    r"(?:failed|passed|skipped|xfailed|xpassed|error|errors|warnings|deselected)"
    r"(?:,\s*\d+\s+"
    r"(?:failed|passed|skipped|xfailed|xpassed|error|errors|warnings|deselected))*)"
)
_PYTEST_NODE_RE = re.compile(
    r"(?m)^(?P<node>[A-Za-z0-9_./-]+\.py::[^\s]+)\s+(?:FAILED|ERROR|XPASS|XFAIL)"
)
_PYTEST_SHORT_NODE_RE = re.compile(
    r"(?m)^_{2,}\s+(?P<node>[A-Za-z0-9_./-]+\.py::[^_\n]+?)\s+_{2,}\s*$"
)
_VALIDATION_ERROR_SUMMARY_RE = re.compile(
    r"(?m)^\s*(?:E\s+)?(?P<summary>"
    r"(?:AssertionError|Error|RuntimeError|TypeError|ValueError|ImportError|"
    r"ModuleNotFoundError|PermissionError|FileNotFoundError|subprocess\.[A-Za-z]+)"
    r"[: ].*)$"
)


def _validation_pytest_totals(output: str) -> str | None:
    matches = list(_PYTEST_TOTALS_RE.finditer(output or ""))
    if not matches:
        return None
    return _validation_receipt_value(matches[-1].group("totals"), limit=160).replace(
        ",_", ","
    )


def _validation_failing_nodes(output: str) -> list[str]:
    nodes: list[str] = []
    for pattern in (_PYTEST_NODE_RE, _PYTEST_SHORT_NODE_RE):
        for match in pattern.finditer(output or ""):
            node = match.group("node").strip()
            if node and node not in nodes:
                nodes.append(node)
    return nodes[:5]


def _validation_error_summary(output: str) -> str:
    for pattern in (_VALIDATION_ERROR_SUMMARY_RE,):
        for match in pattern.finditer(output or ""):
            summary = match.group("summary").strip()
            if summary:
                return _validation_receipt_value(summary, limit=300)
    for line in reversed((output or "").splitlines()):
        stripped = line.strip()
        if stripped:
            return _validation_receipt_value(stripped, limit=300)
    return "none"


def _validation_failure_phase(command: tuple[str, ...], output: str, exit_code: int) -> str:
    lowered = output.lower()
    if command[:2] == ("git", "diff") or "not a git repository" in lowered:
        return "git_metadata"
    if exit_code in (126, 127) or "no such file or directory" in lowered:
        return "process_start"
    if "permission denied" in lowered:
        return "permissions"
    if "error collecting" in lowered or "collected 0 items" in lowered:
        return "collection"
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        return "missing_dependency"
    if "setup" in lowered and "failed" in lowered:
        return "setup"
    if "teardown" in lowered and "failed" in lowered:
        return "teardown"
    if "assertionerror" in lowered or " failed" in lowered:
        return "call"
    return "process"


def _validation_command_receipt_lines(
    index: int, command: tuple[str, ...], exit_code: int, output: str
) -> list[str]:
    status = "passed" if exit_code == 0 else "failed"
    lines = [
        (
            f"step=validation_profile_command_{index} status={status} "
            f"exit_code={exit_code}"
        ),
        f"validation_command_index={index}",
        f"validation_command_text={_validation_command_text(command)}",
        f"exit_code={exit_code}",
        f"status={status}",
        f"validation_output_tail={_validation_receipt_value(_bounded_validation_command_output(output), limit=VALIDATION_FAILED_OUTPUT_LIMIT)}",
    ]
    totals = _validation_pytest_totals(output)
    if totals is not None:
        lines.append(f"validation_pytest_totals={totals}")
    if exit_code != 0:
        lines.extend(_missing_dependency_module_lines(output))
        lines.append(
            f"validation_failure_phase={_validation_failure_phase(command, output, exit_code)}"
        )
        for node in _validation_failing_nodes(output):
            lines.append(f"validation_failing_node={_validation_receipt_value(node, limit=260)}")
        lines.append(f"validation_error_summary={_validation_error_summary(output)}")
    return lines


def _validation_command_failure_lines(
    index: int, command: tuple[str, ...], exit_code: int, output: str
) -> list[str]:
    return _validation_command_receipt_lines(index, command, exit_code, output)


_MISSING_DEPENDENCY_MODULE_RES = (
    re.compile(r"ModuleNotFoundError:\s+No module named ['\"](?P<module>[^'\"]+)['\"]"),
    re.compile(r"ImportError:\s+No module named ['\"](?P<module>[^'\"]+)['\"]"),
)


def _missing_dependency_module_lines(output: str) -> list[str]:
    modules: list[str] = []
    for pattern in _MISSING_DEPENDENCY_MODULE_RES:
        for match in pattern.finditer(output or ""):
            module = match.group("module").strip()
            if module and module not in modules:
                modules.append(module)
    return [f"missing_dependency_module={module}" for module in modules]


def _validation_read_command_value(command: list[str], cwd: Path) -> str:
    code, output = run_command(command, cwd=cwd)
    if code != 0:
        return "unavailable"
    return _validation_receipt_value(output.strip(), limit=180)


def _validation_changed_files(base_sha: str, cwd: Path) -> tuple[list[str], str | None]:
    code, output = run_command(["git", "diff", "--name-only", base_sha, "HEAD", "--"], cwd=cwd)
    if code != 0:
        return [], "changed_file_discovery_failed"
    files: list[str] = []
    for line in output.splitlines():
        safe_file = _safe_changed_file(line)
        if safe_file is not None:
            files.append(safe_file)
    return sorted(dict.fromkeys(files)), None


def _cleanup_validation_runtime_artifacts(cwd: Path) -> str | None:
    try:
        cleanup_runtime_artifacts(cwd, strict=True)
    except OSError:
        return "runtime_artifact_cleanup_failed"
    return None


def _validation_worktree_status(cwd: Path) -> tuple[bool, str]:
    cleanup_reason = _cleanup_validation_runtime_artifacts(cwd)
    if cleanup_reason is not None:
        return False, cleanup_reason
    code, output = run_command(["git", "status", "--short"], cwd=cwd)
    if code != 0:
        return False, "status_failed"
    if output.strip():
        return False, "dirty"
    return True, "clean"


def _validation_write_probe(directory: Path, name: str) -> bool:
    probe = directory / name
    created = False
    try:
        with open(probe, "xb"):
            created = True
        probe.unlink()
        return True
    except OSError:
        if created:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def _validation_git_path(command: list[str], cwd: Path) -> Path | None:
    code, output = run_command(command, cwd=cwd)
    if code != 0:
        return None
    path_text = output.strip()
    if not path_text:
        return None
    path = Path(path_text)
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve()
    except OSError:
        return None


def _path_is_bounded(path: Path, bounds: tuple[Path, ...]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for bound in bounds:
        try:
            resolved.relative_to(bound)
            return True
        except ValueError:
            continue
    return False


def _validation_git_worktree_check(
    validation_path: Path, checkout_path: Path
) -> tuple[bool, list[str], str | None]:
    code, output = run_command(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=validation_path
    )
    is_worktree = code == 0 and output.strip().lower() == "true"
    lines = [f"validation_real_writable_git_worktree={str(False).lower()}"]
    if not is_worktree:
        return False, lines, "validation_not_real_git_worktree"
    try:
        checkout_bound = checkout_path.resolve()
        validation_bound = validation_path.resolve()
    except OSError:
        return False, lines, "validation_path_unresolvable"
    git_dir = _validation_git_path(
        ["git", "rev-parse", "--path-format=absolute", "--git-dir"], validation_path
    )
    common_dir = _validation_git_path(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        validation_path,
    )
    index_path = _validation_git_path(
        ["git", "rev-parse", "--path-format=absolute", "--git-path", "index"],
        validation_path,
    )
    if git_dir is None or common_dir is None or index_path is None:
        return False, lines, "validation_git_metadata_unresolved"
    bounds = (validation_bound, checkout_bound)
    bounded_paths = (git_dir, common_dir, index_path.parent)
    if not all(_path_is_bounded(path, bounds) for path in bounded_paths):
        return False, lines, "validation_git_metadata_outside_bounds"
    if not validation_path.is_dir() or not _validation_write_probe(
        validation_path, ".runner-validation-worktree-write-probe"
    ):
        return False, lines, "validation_worktree_unwritable"
    for probe_dir, probe_name in (
        (git_dir, "runner-validation-gitdir-write-probe"),
        (index_path.parent, "runner-validation-index-write-probe"),
    ):
        if not probe_dir.is_dir() or not _validation_write_probe(probe_dir, probe_name):
            return False, lines, "validation_git_metadata_unwritable"
    lines = [
        f"validation_real_writable_git_worktree={str(True).lower()}",
        f"validation_git_dir={_validation_receipt_value(str(git_dir), limit=180)}",
        f"validation_git_common_dir={_validation_receipt_value(str(common_dir), limit=180)}",
    ]
    return True, lines, None


def _validation_profile_commands(
    profile: str, validated_base_sha: str
) -> tuple[tuple[str, ...], ...]:
    commands: list[tuple[str, ...]] = []
    for command in PR_BRANCH_VALIDATION_PROFILES[profile]:
        commands.append(
            tuple(
                part.replace("{validated_base_sha}", validated_base_sha)
                for part in command
            )
        )
    return tuple(commands)


def _validation_checkout_metadata_lines(
    validation_path: Path, base_ref: str, base_sha: str
) -> tuple[list[str], str | None]:
    changed_files, changed_files_reason = _validation_changed_files(
        base_sha, validation_path
    )
    if changed_files_reason is not None:
        return [
            f"validation_base_ref={_validation_receipt_value(base_ref, limit=80)}",
            f"validation_base_sha={base_sha}",
        ], changed_files_reason
    return [
        f"validation_checkout_head_sha={_validation_read_command_value(['git', 'rev-parse', 'HEAD'], validation_path)}",
        f"validation_base_ref={_validation_receipt_value(base_ref, limit=80)}",
        f"validation_base_sha={base_sha}",
        f"validation_changed_files_count={len(changed_files)}",
        *(f"validation_changed_file={path}" for path in changed_files),
        f"python_version={_validation_receipt_value('.'.join(str(part) for part in sys.version_info[:3]), limit=40)}",
        f"validation_pytest_version={_validation_read_command_value(['python3', '-m', 'pytest', '--version'], validation_path)}",
    ], None


def validate_pr_branch(body: str) -> str:
    task_id = VALIDATE_PR_BRANCH
    request, reason = _pr_branch_validation_metadata(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")
    assert request is not None

    status_lines = [
        f"repository={request.repository}",
        f"pull_request={request.pr_number}",
        f"validation_profile={request.profile}",
    ]
    checkout_path, checkout_block_reason = _pr_branch_validation_checkout_path(
        request.repository
    )
    if checkout_path is None or checkout_block_reason is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={checkout_block_reason or 'checkout_unavailable'}"],
            "not_met",
        )
    status_lines.append(f"checkout_path={checkout_path}")

    try:
        pr_state = _get_pr_branch_validation_state(
            request.repository, request.pr_number
        )
    except Exception:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_pr_metadata status=failed"],
            "not_met",
        )
    status_lines.append("step=read_pr_metadata status=done")

    block_reason = _pr_branch_validation_block_reason(request, pr_state)
    if block_reason is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={block_reason}"],
            "not_met",
        )

    head_sha = str(pr_state["headRefOid"]).lower()
    head_ref = str(pr_state.get("headRefName") or "")
    if head_ref:
        status_lines.append(f"head_ref={head_ref}")
    status_lines.append(f"head_sha={head_sha}")
    base_ref = str(pr_state.get("baseRefName") or "main")
    base_sha = str(pr_state["baseRefOid"]).lower()
    status_lines.append(f"expected_base_sha={request.expected_base_sha}")
    status_lines.append(f"pr_base_sha={base_sha}")
    try:
        validation_path = _ensure_safe_validation_worktree_path(
            request.repository,
            _validation_worktree_path(request.repository, request.pr_number),
        )
    except ValueError:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=validation_worktree_path_unsafe"],
            "not_met",
        )

    try:
        validation_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=prepare_validation_parent status=failed"],
            "not_met",
        )
    status_lines.append("step=prepare_validation_parent status=done")

    if validation_path.exists():
        code, _output = run_command(
            ["git", "worktree", "remove", "--force", str(validation_path)],
            cwd=checkout_path,
        )
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    (
                        "step=remove_validation_worktree status=failed "
                        f"exit_code={code}"
                    ),
                ],
                "not_met",
            )
        status_lines.append("step=remove_validation_worktree status=done")

    pr_ref = f"refs/remotes/origin/pr-validation/{request.pr_number}"
    fetch_refspec = f"+refs/pull/{request.pr_number}/head:{pr_ref}"
    code, _output = run_command(
        ["git", "fetch", "origin", fetch_refspec], cwd=checkout_path
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"step=fetch_pr_head status=failed exit_code={code}"],
            "not_met",
        )
    status_lines.append("step=fetch_pr_head status=done")

    code, output = run_command(
        ["git", "rev-parse", f"{pr_ref}^{{commit}}"], cwd=checkout_path
    )
    if code != 0 or output.strip().lower() != head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_fetched_head status=failed"],
            "not_met",
        )
    status_lines.append("step=verify_fetched_head status=done")

    code, _output = run_command(
        ["git", "worktree", "add", "--detach", str(validation_path), head_sha],
        cwd=checkout_path,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                f"step=checkout_validation_head status=failed exit_code={code}",
            ],
            "not_met",
        )
    status_lines.append("step=checkout_validation_head status=done")

    code, output = run_command(["git", "rev-parse", "HEAD"], cwd=validation_path)
    if code != 0 or output.strip().lower() != head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_validation_head status=failed"],
            "not_met",
        )
    status_lines.append("step=verify_validation_head status=done")

    git_ok, git_lines, git_reason = _validation_git_worktree_check(
        validation_path, checkout_path
    )
    status_lines.extend(git_lines)
    if not git_ok:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={git_reason or 'validation_git_worktree_unusable'}"],
            "not_met",
        )

    metadata_lines, metadata_reason = _validation_checkout_metadata_lines(
        validation_path, base_ref, base_sha
    )
    status_lines.extend(metadata_lines)
    if metadata_reason is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={metadata_reason}"],
            "not_met",
        )
    clean, initial_status = _validation_worktree_status(validation_path)
    status_lines.append(f"validation_initial_status={initial_status}")
    if not clean:
        reason = (
            initial_status
            if initial_status == "runtime_artifact_cleanup_failed"
            else "validation_worktree_not_clean"
        )
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={reason}"],
            "not_met",
        )

    for index, command in enumerate(
        _validation_profile_commands(request.profile, base_sha), 1
    ):
        code, output = _run_validation_profile_command(
            list(command), cwd=validation_path
        )
        status_lines.extend(
            _validation_command_receipt_lines(index, command, code, output)
        )
        if code != 0:
            clean, final_status = _validation_worktree_status(validation_path)
            del clean
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    f"validation_final_status={final_status}",
                ],
                "not_met",
            )

    clean, final_status = _validation_worktree_status(validation_path)
    status_lines.append(f"validation_final_status={final_status}")
    if not clean:
        reason = (
            final_status
            if final_status == "runtime_artifact_cleanup_failed"
            else "validation_worktree_not_clean_after_commands"
        )
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={reason}"],
            "not_met",
        )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _pr_mergeability_inspection_metadata(
    body: str,
) -> tuple[PrMergeabilityInspectionRequest | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    repository = _body_field(metadata, "Repository")
    pr_number = _body_field(metadata, "Pull Request")
    expected_head_sha = _body_field(metadata, "Expected Head SHA")
    if repository is not None and repository != REPO:
        return None, "unsupported_repository"
    if not isinstance(pr_number, str) or not re.fullmatch(r"[1-9]\d*", pr_number):
        return None, "missing_or_invalid_pull_request"
    if (
        expected_head_sha is not None
        and _HEAD_SHA_RE.fullmatch(expected_head_sha) is None
    ):
        return None, "invalid_expected_head_sha"
    return (
        PrMergeabilityInspectionRequest(
            pr_number=int(pr_number),
            expected_head_sha=(
                expected_head_sha.lower()
                if isinstance(expected_head_sha, str)
                else None
            ),
        ),
        None,
    )


def _github_api_json(path: str, query: dict[str, str] | None = None) -> object:
    url = f"https://api.github.com{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "skeleton-runner-maintenance",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8") or "null")


def _github_api_list(path: str, key: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _github_api_json(
            path, {"per_page": "100", "page": str(page)}
        )
        page_items = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(page_items, list):
            raise RuntimeError("GitHub API list response was malformed")
        items.extend(item for item in page_items if isinstance(item, dict))
        if len(page_items) < 100:
            return items
        page += 1


def _get_pr_mergeability_state(pr_number: int) -> dict[str, Any]:
    pr_path = f"/repos/{REPO}/pulls/{pr_number}"
    pr = _github_api_json(pr_path)
    if not isinstance(pr, dict):
        raise RuntimeError("GitHub PR response was malformed")

    files = _github_api_list(f"{pr_path}/files")
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    base_sha = str((pr.get("base") or {}).get("sha") or "")

    compare: dict[str, Any] = {}
    if _HEAD_SHA_RE.fullmatch(base_sha) and _HEAD_SHA_RE.fullmatch(head_sha):
        compare_payload = _github_api_json(
            f"/repos/{REPO}/compare/{base_sha}...{head_sha}"
        )
        if isinstance(compare_payload, dict):
            compare = compare_payload

    combined_status: dict[str, Any] = {}
    check_runs: list[dict[str, Any]] = []
    if _HEAD_SHA_RE.fullmatch(head_sha):
        status_payload = _github_api_json(f"/repos/{REPO}/commits/{head_sha}/status")
        if isinstance(status_payload, dict):
            combined_status = status_payload
        check_runs = _github_api_list(
            f"/repos/{REPO}/commits/{head_sha}/check-runs", key="check_runs"
        )

    return {
        "pr": pr,
        "files": files,
        "compare": compare,
        "combined_status": combined_status,
        "check_runs": check_runs,
    }


def _github_bool_value(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def _validation_summary(
    combined_status: dict[str, Any], check_runs: list[dict[str, Any]]
) -> tuple[str, str]:
    statuses = combined_status.get("statuses")
    status_items = statuses if isinstance(statuses, list) else []
    status_state = str(combined_status.get("state") or "").lower()
    if not status_items and not check_runs:
        return "missing", "validation_missing"

    check_states = {
        str(check.get("status") or "").lower() for check in check_runs
    }
    check_conclusions = {
        str(check.get("conclusion") or "").lower()
        for check in check_runs
        if check.get("conclusion") is not None
    }
    checks_success = not check_runs or (
        check_states <= {"completed"}
        and check_conclusions <= {"success", "neutral", "skipped"}
    )
    statuses_success = not status_items or status_state == "success"
    if statuses_success and checks_success:
        return "success", "none"
    return "not_success", "validation_not_success"


def _pr_mergeability_next_action(
    pr: dict[str, Any],
    compare: dict[str, Any],
    validation_state: str,
    request: PrMergeabilityInspectionRequest,
) -> tuple[str, str, str]:
    state = str(pr.get("state") or "").upper()
    head_sha = str((pr.get("head") or {}).get("sha") or "").lower()
    mergeable = pr.get("mergeable")
    mergeable_state = str(pr.get("mergeable_state") or "").lower()
    behind_by = compare.get("behind_by")
    compare_status = str(compare.get("status") or "").lower()

    if state != "OPEN":
        return "BLOCKED", "pr_not_open", "obsolete_close_or_reopen_request"
    if request.expected_head_sha is not None and head_sha != request.expected_head_sha:
        return "BLOCKED", "expected_head_sha_mismatch", "refresh_inspection_request"
    if pr.get("draft") is True:
        return "BLOCKED", "pr_is_draft", "mark_pr_ready_for_review"
    if (
        isinstance(behind_by, int)
        and behind_by > 0
        or compare_status in {"behind", "diverged"}
        or mergeable_state == "behind"
    ):
        return "BLOCKED", "branch_behind_or_diverged", "refresh_pr_branch"
    if mergeable is False or mergeable_state in {"dirty", "blocked"}:
        return "BLOCKED", "pr_has_merge_conflicts", "resolve_merge_conflicts"
    if validation_state == "missing":
        return "BLOCKED", "validation_missing", "run_required_validation"
    if validation_state != "success":
        return "BLOCKED", "validation_not_success", "wait_for_or_fix_validation"
    if mergeable is True:
        return "DONE", "none", "mark_ready_or_merge"
    return "BLOCKED", "mergeability_unknown", "refresh_mergeability_inspection"


def inspect_pr_mergeability(body: str) -> str:
    task_id = INSPECT_PR_MERGEABILITY
    request, reason = _pr_mergeability_inspection_metadata(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")
    assert request is not None

    status_lines = [f"repository={REPO}", f"pull_request={request.pr_number}"]
    try:
        state = _get_pr_mergeability_state(request.pr_number)
    except Exception:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_pr_metadata status=failed"],
            "not_met",
        )

    pr = state["pr"]
    files = state["files"]
    compare = state["compare"]
    combined_status = state["combined_status"]
    check_runs = state["check_runs"]

    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    if base_repo.get("full_name") != REPO:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=unsupported_repository"],
            "not_met",
        )

    validation_state, validation_reason = _validation_summary(
        combined_status, check_runs
    )
    report_status, reason, next_action = _pr_mergeability_next_action(
        pr, compare, validation_state, request
    )
    changed_files = [
        str(file.get("filename"))
        for file in files
        if isinstance(file.get("filename"), str)
    ]
    mergeable = _github_bool_value(pr.get("mergeable"))
    mergeable_state = str(pr.get("mergeable_state") or "unknown")
    status_lines.extend(
        (
            "step=read_pr_metadata status=done",
            f"pr_state={str(pr.get('state') or '').lower()}",
            f"draft={_github_bool_value(pr.get('draft'))}",
            f"base_branch={base.get('ref')}",
            f"base_sha={base.get('sha')}",
            f"head_branch={head.get('ref')}",
            f"head_sha={head.get('sha')}",
            f"mergeable={mergeable}",
            f"mergeable_state={mergeable_state}",
            f"changed_file_count={len(changed_files)}",
            f"changed_files={','.join(changed_files) if changed_files else '(none)'}",
            f"compare_status={compare.get('status', 'unknown')}",
            f"ahead_by={compare.get('ahead_by', 'unknown')}",
            f"behind_by={compare.get('behind_by', 'unknown')}",
            f"validation_state={validation_state}",
            f"reason={reason if reason != 'none' else validation_reason}",
            f"next_action={next_action}",
        )
    )
    return _maintenance_report(
        report_status,
        task_id,
        status_lines,
        "met" if report_status == "DONE" else "not_met",
    )


def _safe_issue_publish_file_path(path: str) -> bool:
    relative_path = Path(path)
    return (
        path == path.strip()
        and path != ""
        and not path.startswith(".github/workflows/")
        and not relative_path.is_absolute()
        and ".." not in relative_path.parts
        and _SAFE_CHANGED_FILE_RE.fullmatch(path) is not None
    )


def _issue_publish_allowed_files(metadata: str) -> tuple[frozenset[str], str | None]:
    yaml_allowed_files = _metadata_yaml_value(metadata, "allowed_files")
    if yaml_allowed_files is not None:
        if not isinstance(yaml_allowed_files, list):
            return frozenset(), "invalid_allowed_files"
        allowed_files_from_yaml: list[str] = []
        for allowed_path in yaml_allowed_files:
            if not isinstance(allowed_path, str) or not _safe_issue_publish_file_path(
                allowed_path
            ):
                return frozenset(), "invalid_allowed_files"
            allowed_files_from_yaml.append(allowed_path)
        if not allowed_files_from_yaml:
            return frozenset(), "missing_allowed_files"
        if len(set(allowed_files_from_yaml)) != len(allowed_files_from_yaml):
            return frozenset(), "invalid_allowed_files"
        return frozenset(allowed_files_from_yaml), None

    lines = (metadata or "").splitlines()
    for index, line in enumerate(lines):
        if re.fullmatch(r"\s*(?:Allowed Files|allowed_files):\s*", line):
            allowed_files: list[str] = []
            for item in lines[index + 1 :]:
                if re.fullmatch(r"\s*[A-Za-z][A-Za-z ]+:\s*.*", item):
                    break
                if re.fullmatch(r"\s*```\s*(?:yaml|yml)?\s*", item):
                    break
                match = re.fullmatch(r"\s*-\s+(?P<path>\S(?:.*\S)?)\s*", item)
                if match is None:
                    if item.strip():
                        return frozenset(), "invalid_allowed_files"
                    continue
                allowed_path = match.group("path")
                if not _safe_issue_publish_file_path(allowed_path):
                    return frozenset(), "invalid_allowed_files"
                allowed_files.append(allowed_path)
            if not allowed_files:
                return frozenset(), "missing_allowed_files"
            if len(set(allowed_files)) != len(allowed_files):
                return frozenset(), "invalid_allowed_files"
            return frozenset(allowed_files), None
    return frozenset(), "missing_allowed_files"


def _issue_publish_pr_title(
    metadata: str, source_issue_number: int
) -> tuple[str, str | None]:
    pr_title = _body_field(metadata, "PR Title")
    if pr_title is None:
        return f"Runner task #{source_issue_number}", None
    if (
        not pr_title
        or len(pr_title) > 180
        or any(ord(character) < 32 for character in pr_title)
        or _ENV_ASSIGNMENT_LINE_RE.fullmatch(pr_title) is not None
    ):
        return "", "invalid_pr_title"
    return pr_title, None


def _safe_issue_publish_branch_name(branch: str) -> bool:
    return (
        bool(branch)
        and branch == branch.strip()
        and branch.startswith("runner/issue-")
        and ".." not in branch
        and not branch.startswith("/")
        and not branch.endswith("/")
        and "//" not in branch
        and re.fullmatch(r"[A-Za-z0-9._/-]+", branch) is not None
    )


def _safe_issue_publish_base_branch(branch: str) -> bool:
    return (
        bool(branch)
        and branch == branch.strip()
        and not branch.startswith(("/", "-"))
        and not branch.endswith("/")
        and ".." not in branch
        and "//" not in branch
        and re.fullmatch(r"[A-Za-z0-9._/-]+", branch) is not None
    )


def _issue_publish_bool_field(
    metadata: str, field: str, *, default: bool | None = None
) -> tuple[bool | None, str | None]:
    value = _body_field(metadata, field)
    if value is None:
        return default, None
    lowered = value.lower()
    if lowered == "true":
        return True, None
    if lowered == "false":
        return False, None
    return None, f"invalid_{field.lower().replace(' ', '_')}"


def _issue_worktree_publish_inspection_metadata(
    body: str,
    *,
    require_repository: bool = False,
    explicit_recovery_route: bool = False,
    target_project_route: bool = False,
) -> tuple[IssueWorktreePublishInspectionRequest | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    repository = None
    target_project = "skeleton"
    target_worktree_root: Path | None = None
    if target_project_route:
        if (
            _body_field(metadata, "Worktree Path") is not None
            or _body_field(metadata, "Issue Worktree") is not None
            or _body_field(metadata, "Source Worktree") is not None
        ):
            return None, "path_input_not_allowed"
        raw_target_project = _body_field(metadata, "Target Project")
        raw_target_repository = _body_field(metadata, "Target Repository")
        if raw_target_project is None:
            return None, "missing_target_project"
        if raw_target_repository is None:
            return None, "missing_target_repository"
        project_tree = load_runner_project_tree()
        try:
            project_from_project = get_project(project_tree, raw_target_project)
            project_from_repository = get_project_by_repo(
                project_tree, raw_target_repository
            )
        except (KeyError, ValueError):
            return None, "unsupported_target_project_or_repository"
        if project_from_project != project_from_repository:
            return None, "target_project_repository_mismatch"
        if project_from_project.get("public") is not True:
            return None, "target_repository_not_public"
        if project_from_project.get("runner_enabled") is not True:
            return None, "target_project_runner_disabled"
        if project_from_project.get("repo") != raw_target_repository:
            return None, "target_repository_metadata_mismatch"
        target_project = raw_target_project
        repository = raw_target_repository
        target_worktree_root = Path(project_from_project["worktree_root"])
    else:
        repository = (
            _body_field(metadata, "Target Repository")
            if explicit_recovery_route
            else _body_field(metadata, "Repository")
        )
    source_issue = _body_field(metadata, "Source Issue")
    expected_branch = (
        _body_field(metadata, "Output Branch")
        if explicit_recovery_route or target_project_route
        else _body_field(metadata, "Expected Branch")
    )
    base_branch = _body_field(metadata, "Base Branch") or "main"
    draft_pr, draft_pr_reason = _issue_publish_bool_field(
        metadata, "Draft PR", default=True
    )
    allowed_files, allowed_files_reason = _issue_publish_allowed_files(metadata)

    if not target_project_route and require_repository and repository != REPO:
        return None, "unsupported_repository"
    if not target_project_route and repository is not None and repository != REPO:
        return None, "unsupported_repository"
    if (explicit_recovery_route or target_project_route) and repository is None:
        return None, "missing_target_repository"
    if not isinstance(source_issue, str) or re.fullmatch(r"[1-9]\d*", source_issue) is None:
        return None, "missing_or_invalid_source_issue"
    source_issue_number = int(source_issue)
    required_branch = f"runner/issue-{source_issue_number}"
    if explicit_recovery_route or target_project_route:
        if not isinstance(expected_branch, str) or not _safe_issue_publish_branch_name(
            expected_branch
        ):
            return None, "missing_or_invalid_output_branch"
    elif expected_branch != required_branch:
        return None, "missing_or_invalid_expected_branch"
    if (explicit_recovery_route or target_project_route) and expected_branch != required_branch:
        return None, "output_branch_mismatch"
    if not _safe_issue_publish_base_branch(base_branch):
        return None, "missing_or_invalid_base_branch"
    if (explicit_recovery_route or target_project_route) and base_branch != "main":
        return None, "unsupported_base_branch"
    if draft_pr_reason is not None:
        return None, draft_pr_reason
    if (explicit_recovery_route or target_project_route) and draft_pr is not True:
        return None, "draft_pr_required"
    if allowed_files_reason is not None:
        return None, allowed_files_reason
    pr_title, pr_title_reason = _issue_publish_pr_title(metadata, source_issue_number)
    if pr_title_reason is not None:
        return None, pr_title_reason

    return (
        IssueWorktreePublishInspectionRequest(
            repository=repository or REPO,
            source_issue=source_issue_number,
            expected_branch=expected_branch,
            allowed_files=allowed_files,
            pr_title=pr_title,
            base_branch=base_branch,
            draft_pr=bool(draft_pr),
            target_project=target_project,
            worktree_root=target_worktree_root,
            target_project_route=target_project_route,
        ),
        None,
    )


_EXISTING_PR_PUBLISH_ALLOWED_METADATA_FIELDS = frozenset(
    (
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Source Issue",
        "Expected Source Branch",
        "Pull Request",
        "Expected PR Head SHA",
        "Expected PR Head Branch",
        "Operator Approval",
        "Allowed Files",
    )
)


REGISTERED_WORKTREE_OVERLAY_PACKETS: dict[str, RegisteredWorktreeOverlayPacket] = {
    "home_edge_1640_to_pr_1638": RegisteredWorktreeOverlayPacket(
        packet_id="home_edge_1640_to_pr_1638",
        source_issue=1640,
        source_branch="runner/issue-1640",
        pr_number=1638,
        target_branch="runner/issue-1630",
        target_head_sha="1f6c0463682f7c36a8b3f3e44182571d8323ee19",
        allowed_files=(
            "core/home_edge/visual_capture.py",
            "scripts/runner_poll_github_tasks.py",
            "tests/test_home_edge_visual_capture.py",
            "tests/test_runner_poll_github_tasks.py",
            "tests/test_home_edge_profile.py",
        ),
    ),
    "docs_1668_to_pr_1670": RegisteredWorktreeOverlayPacket(
        packet_id="docs_1668_to_pr_1670",
        source_issue=1668,
        source_branch="runner/issue-1668",
        pr_number=1670,
        target_branch="runner/issue-1668",
        target_head_sha="6efc9e389c50b59a46866ed041a914aa404a40b0",
        allowed_files=(
            "docs/NOTEBOOKLM_SOURCEPACK.md",
            "docs/OPEN_WORK_TRIAGE.md",
            "docs/RUNNER_QUEUE_STATUS.md",
            "projects/skeleton/STATE.yaml",
        ),
    ),
}


_REGISTERED_WORKTREE_OVERLAY_ALLOWED_METADATA_FIELDS = frozenset(
    ("Mode", "Maintenance Task ID", "Recovery Packet", "Operator Approval")
)


def _metadata_field_names(metadata: str) -> frozenset[str]:
    names: set[str] = set()
    for line in (metadata or "").splitlines():
        match = re.fullmatch(r"\s*(?P<name>[A-Za-z][A-Za-z ]+):\s*.*", line)
        if match is not None:
            names.add(match.group("name"))
    return frozenset(names)


def _existing_pr_publish_metadata(
    body: str,
) -> tuple[IssueWorktreeExistingPrPublishRequest | None, str | None]:
    metadata = _metadata_before_task(body)
    fields = _metadata_field_names(metadata)
    if not fields <= _EXISTING_PR_PUBLISH_ALLOWED_METADATA_FIELDS:
        return None, "unsupported_metadata_field"
    repository = _body_field(metadata, "Repository")
    source_issue = _body_field(metadata, "Source Issue")
    expected_source_branch = _body_field(metadata, "Expected Source Branch")
    pr_number = _body_field(metadata, "Pull Request")
    expected_pr_head_sha = _body_field(metadata, "Expected PR Head SHA")
    expected_pr_head_branch = _body_field(metadata, "Expected PR Head Branch")
    operator_approval = _body_field(metadata, "Operator Approval")
    allowed_files, allowed_files_reason = _issue_publish_allowed_files(metadata)

    if repository != REPO:
        return None, "unsupported_repository"
    if not isinstance(source_issue, str) or re.fullmatch(r"[1-9]\d*", source_issue) is None:
        return None, "missing_or_invalid_source_issue"
    source_issue_number = int(source_issue)
    required_source_branch = f"runner/issue-{source_issue_number}"
    if expected_source_branch != required_source_branch:
        return None, "missing_or_invalid_expected_source_branch"
    if not isinstance(pr_number, str) or re.fullmatch(r"[1-9]\d*", pr_number) is None:
        return None, "missing_or_invalid_pull_request"
    if (
        not isinstance(expected_pr_head_sha, str)
        or _HEAD_SHA_RE.fullmatch(expected_pr_head_sha) is None
    ):
        return None, "invalid_expected_pr_head_sha"
    if (
        not isinstance(expected_pr_head_branch, str)
        or not _safe_issue_publish_branch_name(expected_pr_head_branch)
    ):
        return None, "missing_or_invalid_expected_pr_head_branch"
    if not expected_pr_head_branch.startswith("runner/issue-"):
        return None, "missing_or_invalid_expected_pr_head_branch"
    if operator_approval != PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR:
        return None, "missing_operator_approval"
    if allowed_files_reason is not None:
        return None, allowed_files_reason
    return (
        IssueWorktreeExistingPrPublishRequest(
            repository=repository,
            source_issue=source_issue_number,
            expected_source_branch=expected_source_branch,
            pr_number=int(pr_number),
            expected_pr_head_sha=expected_pr_head_sha.lower(),
            expected_pr_head_branch=expected_pr_head_branch,
            allowed_files=allowed_files,
        ),
        None,
    )


def _issue_publish_worktree_path(issue_number: int) -> Path:
    return worktree_root() / f"issue-{issue_number}"


def _ensure_safe_issue_publish_worktree_path(path: str | Path) -> Path:
    root = worktree_root().resolve(strict=False)
    candidate = Path(path).expanduser().resolve(strict=False)
    if candidate == root:
        raise ValueError("issue worktree cannot be the worktree root")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("issue worktree is outside runner worktree root") from exc
    return candidate


_PROTECTED_EXACT_SOURCE_REPAIR_ALLOWED_METADATA_FIELDS = frozenset(
    (
        "Mode",
        "Maintenance Task ID",
        "Target Repository",
        "Selected Repository",
        "Repository",
        "Source Issue",
        "Issue",
        "Output Branch",
        "Expected Branch",
        "Expected Source Branch",
        "Source Branch",
        "Source Ref",
        "Source Reference",
        "Expected Source SHA",
        "Expected Source Head SHA",
        "Expected Head SHA",
        "Source SHA",
        "Operator Approval",
    )
)


def _first_task_fenced_block(body: str) -> str:
    match = re.search(
        r"```task[ \t]*\n(?P<task>.*?)(?:\n```|$)",
        body or "",
        re.DOTALL,
    )
    return match.group("task") if match is not None else ""


def _protected_exact_raw_field(
    text: str, aliases: frozenset[str]
) -> tuple[str | None, str | None, str | None]:
    for line in (text or "").splitlines():
        match = re.fullmatch(
            r"\s*(?P<name>[A-Za-z][A-Za-z ]+):(?P<space> ?)(?P<value>.*)",
            line,
        )
        if match is None:
            continue
        name = match.group("name")
        if name not in aliases:
            continue
        value = match.group("value")
        if value != value.strip() or not value:
            return name, None, f"invalid_{name.lower().replace(' ', '_')}"
        return name, value, None
    return None, None, None


def _protected_exact_source_field(
    task_text: str,
    metadata: str,
    aliases: tuple[str, ...],
) -> tuple[str | None, str | None]:
    alias_set = frozenset(aliases)
    for text in (task_text, metadata):
        _name, value, reason = _protected_exact_raw_field(text, alias_set)
        if reason is not None:
            return None, reason
        if value is not None:
            return value, None
    return None, None


def _safe_protected_exact_source_ref(ref: str) -> bool:
    return (
        bool(ref)
        and ref == ref.strip()
        and not ref.startswith(("/", "-"))
        and not ref.endswith("/")
        and ".." not in ref
        and "//" not in ref
        and re.fullmatch(r"[A-Za-z0-9._/-]+", ref) is not None
    )


def _canonical_protected_exact_source_ref(ref: str) -> str:
    if ref.startswith("refs/heads/"):
        return ref.removeprefix("refs/heads/")
    if ref.startswith("origin/"):
        return ref.removeprefix("origin/")
    return ref


def _protected_exact_source_repair_metadata(
    body: str,
) -> tuple[ProtectedExactSourceWorktreeRepairRequest | None, str | None]:
    metadata = _metadata_before_task(body)
    fields = _metadata_field_names(metadata)
    if not fields <= _PROTECTED_EXACT_SOURCE_REPAIR_ALLOWED_METADATA_FIELDS:
        return None, "unsupported_metadata_field"

    task_text = _first_task_fenced_block(body)
    target_repository, reason = _protected_exact_source_field(
        task_text, metadata, ("Target Repository", "Selected Repository", "Repository")
    )
    if reason is not None:
        return None, reason
    target_repository = target_repository or REPO
    if target_repository not in ALLOWED_TARGET_REPOSITORIES:
        return None, "unsupported_repository"

    source_issue, reason = _protected_exact_source_field(
        task_text, metadata, ("Source Issue", "Issue")
    )
    if reason is not None:
        return None, reason
    if not isinstance(source_issue, str) or re.fullmatch(r"[1-9]\d*", source_issue) is None:
        return None, "missing_or_invalid_source_issue"
    source_issue_number = int(source_issue)
    required_branch = issue_branch(source_issue_number)

    output_branch, reason = _protected_exact_source_field(
        task_text,
        metadata,
        ("Output Branch", "Expected Branch", "Expected Source Branch", "Source Branch"),
    )
    if reason is not None:
        return None, reason
    output_branch = output_branch or required_branch
    if output_branch != required_branch:
        return None, "source_issue_branch_mismatch"
    if not _safe_issue_publish_branch_name(output_branch):
        return None, "missing_or_invalid_output_branch"

    source_ref, reason = _protected_exact_source_field(
        task_text,
        metadata,
        ("Source Ref", "Source Reference", "Expected Source Branch", "Source Branch"),
    )
    if reason is not None:
        return None, reason
    source_ref = source_ref or output_branch
    source_ref = _canonical_protected_exact_source_ref(source_ref)
    if _HEAD_SHA_RE.fullmatch(source_ref) is None and not _safe_protected_exact_source_ref(
        source_ref
    ):
        return None, "missing_or_invalid_source_ref"

    expected_source_sha, reason = _protected_exact_source_field(
        task_text,
        metadata,
        (
            "Expected Source SHA",
            "Expected Source Head SHA",
            "Expected Head SHA",
            "Source SHA",
        ),
    )
    if reason is not None:
        return None, reason
    if (
        not isinstance(expected_source_sha, str)
        or _HEAD_SHA_RE.fullmatch(expected_source_sha) is None
    ):
        return None, "missing_or_invalid_expected_source_sha"

    return (
        ProtectedExactSourceWorktreeRepairRequest(
            target_repository=target_repository,
            source_issue=source_issue_number,
            output_branch=output_branch,
            source_ref=source_ref.lower()
            if _HEAD_SHA_RE.fullmatch(source_ref) is not None
            else source_ref,
            expected_source_sha=expected_source_sha.lower(),
        ),
        None,
    )


def _protected_exact_source_worktree_path(
    request: ProtectedExactSourceWorktreeRepairRequest,
) -> Path:
    return ensure_safe_target_repository_worktree_path(
        request.target_repository,
        target_repository_issue_worktree_path(
            request.target_repository, request.source_issue
        ),
    )


def _protected_exact_source_resolve(
    request: ProtectedExactSourceWorktreeRepairRequest, checkout_path: Path
) -> str | None:
    if _HEAD_SHA_RE.fullmatch(request.source_ref) is not None:
        code, _output = run_command(
            ["git", "cat-file", "-e", f"{request.expected_source_sha}^{{commit}}"],
            cwd=checkout_path,
            timeout=PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS,
        )
        return None if code == 0 and request.source_ref == request.expected_source_sha else "expected_source_sha_mismatch"

    code, _output = run_command(
        [
            "git",
            "fetch",
            "origin",
            f"{request.source_ref}:refs/remotes/origin/{request.source_ref}",
        ],
        cwd=checkout_path,
        timeout=PROTECTED_SOURCE_FETCH_TIMEOUT_SECONDS,
    )
    if code != 0:
        return "source_fetch_failed"
    code, output = run_command(
        ["git", "rev-parse", f"refs/remotes/origin/{request.source_ref}^{{commit}}"],
        cwd=checkout_path,
        timeout=PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS,
    )
    resolved_lines = _git_status_path_lines(output) if code == 0 else []
    resolved_sha = resolved_lines[0].lower() if resolved_lines else ""
    if resolved_sha != request.expected_source_sha:
        return "expected_source_sha_mismatch"
    return None


def repair_protected_exact_source_worktree(body: str) -> str:
    task_id = REPAIR_PROTECTED_EXACT_SOURCE_WORKTREE
    request, reason = _protected_exact_source_repair_metadata(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")
    assert request is not None
    status_lines = [
        f"target_repository={request.target_repository}",
        f"source_issue={request.source_issue}",
        f"output_branch={request.output_branch}",
        f"source_ref={request.source_ref}",
        f"expected_source_sha={request.expected_source_sha}",
    ]
    try:
        checkout_path = target_repository_checkout_path(request.target_repository)
        worktree_path = _protected_exact_source_worktree_path(request)
    except ValueError:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=target_repository_route_invalid"], "not_met"
        )
    if worktree_path.name != f"issue-{request.source_issue}":
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=issue_worktree_path_mismatch"], "not_met"
        )

    resolve_reason = _protected_exact_source_resolve(request, checkout_path)
    if resolve_reason is not None:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, f"reason={resolve_reason}"], "not_met"
        )
    status_lines.append("step=resolve_source status=done")

    if worktree_path.exists():
        existing_branch = ""
        existing_head = ""
        for command, step in (
            (["git", "status", "--porcelain"], "read_existing_status"),
            (["git", "branch", "--show-current"], "read_existing_branch"),
            (["git", "rev-parse", "HEAD"], "read_existing_head"),
        ):
            code, output = run_command(
                command,
                cwd=worktree_path,
                timeout=PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS,
            )
            if code != 0:
                return _maintenance_report(
                    "BLOCKED", task_id, [*status_lines, f"step={step} status=failed"], "not_met"
                )
            if command[1] == "status" and output.strip():
                return _maintenance_report(
                    "BLOCKED", task_id, [*status_lines, "reason=reused_worktree_dirty"], "not_met"
                )
            if command[1] == "branch":
                existing_branch = output.strip()
            if command[1] == "rev-parse":
                existing_head = output.strip().lower()
        if (
            existing_branch == request.output_branch
            and existing_head == request.expected_source_sha
        ):
            status_lines.extend(
                (
                    f"current_branch={request.output_branch}",
                    "step=verify_existing_head status=done",
                )
            )
            return _maintenance_report("DONE", task_id, status_lines, "met")
        code, _output = run_command(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=checkout_path,
            timeout=PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS,
        )
        if code != 0:
            return _maintenance_report(
                "BLOCKED", task_id, [*status_lines, "reason=reused_worktree_cleanup_failed"], "not_met"
            )
        status_lines.append("step=cleanup_reused_worktree status=done")

    try:
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=worktree_root_unavailable"], "not_met"
        )
    code, _output = run_command(
        [
            "git",
            "worktree",
            "add",
            "-B",
            request.output_branch,
            str(worktree_path),
            request.expected_source_sha,
        ],
        cwd=checkout_path,
        timeout=PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=worktree_add_failed"], "not_met"
        )
    for command, expected, reason_token in (
        (["git", "branch", "--show-current"], request.output_branch, "source_branch_mismatch"),
        (["git", "rev-parse", "HEAD"], request.expected_source_sha, "head_sha_mismatch"),
    ):
        code, output = run_command(
            command,
            cwd=worktree_path,
            timeout=PROTECTED_SOURCE_GIT_READ_TIMEOUT_SECONDS,
        )
        actual = output.strip().lower() if command[1] == "rev-parse" else output.strip()
        if code != 0 or actual != expected:
            return _maintenance_report(
                "BLOCKED", task_id, [*status_lines, f"reason={reason_token}"], "not_met"
            )
    status_lines.extend(
        (
            "step=add_worktree status=done",
            "step=verify_head status=done",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _target_project_issue_worktree_path(
    request: IssueWorktreePublishInspectionRequest,
) -> Path:
    if request.worktree_root is None:
        raise ValueError("target project worktree root is missing")
    return request.worktree_root / f"issue-{request.source_issue}"


def _ensure_safe_target_project_issue_publish_worktree_path(
    request: IssueWorktreePublishInspectionRequest,
) -> Path:
    if request.worktree_root is None:
        raise ValueError("target project worktree root is missing")
    root = request.worktree_root.resolve(strict=False)
    candidate = _target_project_issue_worktree_path(request).resolve(strict=False)
    if candidate == root:
        raise ValueError("issue worktree cannot be the target project worktree root")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("issue worktree is outside target project worktree root") from exc
    if candidate.name != f"issue-{request.source_issue}":
        raise ValueError("issue worktree path does not end with the source issue")
    return candidate


def _git_status_path_lines(output: str) -> list[str]:
    return [line.strip() for line in (output or "").splitlines() if line.strip()]


_ISSUE_WORKTREE_ID_RE = re.compile(r"issue-[1-9]\d*")


def _body_list_items(metadata: str, field_names: tuple[str, ...]) -> list[str] | None:
    field_pattern = "|".join(re.escape(field_name) for field_name in field_names)
    for index, line in enumerate((metadata or "").splitlines()):
        if re.fullmatch(rf"\s*(?:{field_pattern}):\s*", line):
            items: list[str] = []
            for item in (metadata or "").splitlines()[index + 1 :]:
                if re.fullmatch(r"\s*[A-Za-z][A-Za-z ]+:\s*.*", item):
                    break
                match = re.fullmatch(r"\s*-\s+(?P<value>\S(?:.*\S)?)\s*", item)
                if match is None:
                    if item.strip():
                        return []
                    continue
                items.append(match.group("value"))
            return items
    return None


def _stale_clean_skeleton_worktree_quarantine_metadata(
    body: str,
) -> tuple[StaleCleanSkeletonWorktreeQuarantineRequest | None, str | None]:
    metadata = (body or "").split("```task", 1)[0]
    repository = _body_field(metadata, "Repository")
    if repository is not None and repository != REPO:
        return None, "unsupported_repository"

    listed_ids = _body_list_items(
        metadata, ("Issue Worktrees", "Worktree IDs", "Worktrees")
    )
    if listed_ids is None or not listed_ids:
        return None, "missing_worktree_ids"
    if any(_ISSUE_WORKTREE_ID_RE.fullmatch(item) is None for item in listed_ids):
        return None, "invalid_worktree_ids"
    if len(set(listed_ids)) != len(listed_ids):
        return None, "duplicate_worktree_ids"

    protected_items = _body_list_items(
        metadata, ("Protected IDs", "Protected Worktrees")
    )
    protected_ids = frozenset(protected_items or ())
    if any(_ISSUE_WORKTREE_ID_RE.fullmatch(item) is None for item in protected_ids):
        return None, "invalid_protected_ids"

    return (
        StaleCleanSkeletonWorktreeQuarantineRequest(
            worktree_ids=tuple(listed_ids),
            protected_ids=protected_ids,
        ),
        None,
    )


def _quarantine_issue_worktree_path(worktree_id: str) -> Path:
    return worktree_root() / worktree_id


def _ensure_safe_quarantine_issue_worktree_path(path: str | Path) -> Path:
    root = worktree_root().resolve(strict=False)
    candidate = Path(path).expanduser().resolve(strict=False)
    if root != DEFAULT_WORKTREE_ROOT.resolve(strict=False):
        raise ValueError("quarantine task is restricted to the Skeleton worktree root")
    if candidate == root:
        raise ValueError("issue worktree cannot be the worktree root")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("issue worktree is outside runner worktree root") from exc
    if (
        candidate.name != Path(path).name
        or _ISSUE_WORKTREE_ID_RE.fullmatch(candidate.name) is None
    ):
        raise ValueError("issue worktree path does not end with an issue id")
    return candidate


def _bounded_quarantine_remove_failed_output(output: str) -> str:
    sanitized = _sanitize_validation_command_output(output)
    if not sanitized:
        return "(no stderr)"
    if len(sanitized) <= QUARANTINE_REMOVE_FAILED_OUTPUT_LIMIT:
        return sanitized
    marker = f"\n{QUARANTINE_REMOVE_FAILED_OUTPUT_TRUNCATED_MARKER}"
    return (
        sanitized[: QUARANTINE_REMOVE_FAILED_OUTPUT_LIMIT - len(marker)].rstrip()
        + marker
    )


def _git_worktree_list_porcelain_registers_path(output: str, path: Path) -> bool:
    expected = str(path)
    for line in (output or "").splitlines():
        if line.startswith("worktree ") and line.removeprefix("worktree ") == expected:
            return True
    return False


def _git_worktree_remove_output_says_not_working_tree(output: str) -> bool:
    return "not a working tree" in (output or "").lower()


def _quarantine_remove_failed_report_lines(
    worktree_id: str,
    worktree_path: Path,
    exit_code: int,
    output: str,
    *,
    action: str = "remove_failed",
    list_code: int | None = None,
    list_output: str | None = None,
) -> list[str]:
    lines = [
        f"worktree={worktree_id} action={action} exit_code={exit_code}",
        "remove_stderr_start",
        _bounded_quarantine_remove_failed_output(output),
        "remove_stderr_end",
    ]
    if list_code is None:
        list_code, list_output = run_command(
            ["git", "worktree", "list", "--porcelain"]
        )
    if list_code == 0:
        registered = _git_worktree_list_porcelain_registers_path(
            list_output or "", worktree_path
        )
        lines.append(f"git_worktree_list_registers_path={str(registered).lower()}")
    else:
        lines.append(f"git_worktree_list_status=failed exit_code={list_code}")
    return lines


def quarantine_stale_clean_skeleton_worktrees(body: str) -> str:
    task_id = QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES
    request, reason = _stale_clean_skeleton_worktree_quarantine_metadata(body)
    if reason is not None:
        return _maintenance_report("BLOCKED", task_id, [f"reason={reason}"], "not_met")
    assert request is not None

    status_lines = [
        f"repository={REPO}",
        f"worktree_root={worktree_root()}",
        f"listed_worktrees_count={len(request.worktree_ids)}",
        f"protected_worktrees_count={len(request.protected_ids)}",
    ]
    removed_count = 0
    skipped_count = 0
    for worktree_id in request.worktree_ids:
        status_lines.append(f"worktree_id={worktree_id}")
        if worktree_id in request.protected_ids:
            skipped_count += 1
            status_lines.append(
                f"worktree={worktree_id} action=skipped reason=protected"
            )
            continue

        try:
            worktree_path = _ensure_safe_quarantine_issue_worktree_path(
                _quarantine_issue_worktree_path(worktree_id)
            )
        except ValueError:
            skipped_count += 1
            status_lines.append(
                f"worktree={worktree_id} action=skipped reason=unsafe_path"
            )
            continue

        if not worktree_path.exists():
            skipped_count += 1
            status_lines.append(f"worktree={worktree_id} action=skipped reason=missing")
            continue
        if not (worktree_path / ".git").exists():
            skipped_count += 1
            status_lines.append(
                f"worktree={worktree_id} action=skipped reason=git_missing"
            )
            continue

        code, output = run_command(
            ["git", "remote", "get-url", "origin"], cwd=worktree_path
        )
        if code != 0:
            skipped_count += 1
            status_lines.append(
                f"worktree={worktree_id} action=skipped reason=origin_read_failed"
            )
            continue
        if not _remote_url_matches_project_repo(output, REPO):
            skipped_count += 1
            status_lines.append(
                f"worktree={worktree_id} action=skipped reason=wrong_remote"
            )
            continue

        code, output = run_command(["git", "status", "--porcelain"], cwd=worktree_path)
        if code != 0:
            skipped_count += 1
            status_lines.append(
                f"worktree={worktree_id} action=skipped reason=status_read_failed"
            )
            continue
        if output.strip():
            skipped_count += 1
            status_lines.append(f"worktree={worktree_id} action=skipped reason=dirty")
            continue

        code, output = run_command(["git", "worktree", "remove", str(worktree_path)])
        if code != 0:
            registered = None
            list_code, list_output = run_command(
                ["git", "worktree", "list", "--porcelain"]
            )
            if list_code == 0:
                registered = _git_worktree_list_porcelain_registers_path(
                    list_output, worktree_path
                )
            if (
                registered is False
                and _git_worktree_remove_output_says_not_working_tree(output)
            ):
                skipped_count += 1
                status_lines.extend(
                    _quarantine_remove_failed_report_lines(
                        worktree_id,
                        worktree_path,
                        code,
                        output,
                        action="skipped_unregistered",
                        list_code=list_code,
                        list_output=list_output,
                    )
                )
                continue
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    *_quarantine_remove_failed_report_lines(
                        worktree_id,
                        worktree_path,
                        code,
                        output,
                        list_code=list_code,
                        list_output=list_output,
                    ),
                ],
                "not_met",
            )
        removed_count += 1
        status_lines.append(f"worktree={worktree_id} action=removed")

    status_lines.extend(
        (
            f"removed_worktrees_count={removed_count}",
            f"skipped_worktrees_count={skipped_count}",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _is_ignored_issue_publish_untracked_path(path: str) -> bool:
    return path == ".codex" or path.startswith(".codex/")


def _issue_worktree_publish_existing_pr_url(
    request: IssueWorktreePublishInspectionRequest, worktree_path: Path
) -> IssueWorktreePublishExistingPrLookup:
    code, output = run_command(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            request.repository,
            "--head",
            request.expected_branch,
            "--state",
            "open",
            "--json",
            "url",
            "--jq",
            ".[0].url // \"\"",
        ],
        cwd=worktree_path,
    )
    if code != 0:
        return IssueWorktreePublishExistingPrLookup(
            pr_url=None, reason="existing_pr_lookup_unavailable"
        )
    pr_url = output.strip()
    if pr_url and _PUBLIC_GITHUB_PR_URL_RE.fullmatch(pr_url) is None:
        return IssueWorktreePublishExistingPrLookup(
            pr_url=None, reason="existing_pr_lookup_unavailable"
        )
    if pr_url:
        return IssueWorktreePublishExistingPrLookup(
            pr_url=pr_url, reason="existing_pr_found"
        )
    return IssueWorktreePublishExistingPrLookup(
        pr_url=None, reason="existing_pr_not_found"
    )


def _issue_worktree_publish_override_result(
    metadata: str, request: IssueWorktreePublishInspectionRequest
) -> tuple[str, str | None]:
    raw_override = _body_field(metadata, "Publish Override")
    if raw_override is None:
        return "publish_override_missing", None
    try:
        override = json.loads(raw_override)
    except json.JSONDecodeError:
        return "publish_override_malformed", None
    if not isinstance(override, dict):
        return "publish_override_malformed", None
    expected_keys = {
        "action",
        "target_repository",
        "source_issue",
        "output_branch",
        "base_branch",
        "allowed_files",
        "draft_pr",
    }
    if set(override) != expected_keys:
        return "publish_override_malformed", None
    allowed_files = override.get("allowed_files")
    if (
        override.get("action") != PUBLISH_EXISTING_ISSUE_WORKTREE
        or override.get("target_repository") != request.repository
        or override.get("source_issue") != request.source_issue
        or override.get("output_branch") != request.expected_branch
        or override.get("base_branch") != request.base_branch
        or override.get("draft_pr") is not True
        or not isinstance(allowed_files, list)
        or not all(isinstance(path, str) for path in allowed_files)
        or any(not _safe_issue_publish_file_path(path) for path in allowed_files)
        or len(set(allowed_files)) != len(allowed_files)
        or set(allowed_files) != set(request.allowed_files)
    ):
        return "publish_override_scope_mismatch", None
    return "publish_override_valid", one_time_override_hash(override)


def _issue_worktree_publish_remote_branch_absent(
    request: IssueWorktreePublishInspectionRequest, worktree_path: Path
) -> bool:
    code, output = run_command(
        ["git", "ls-remote", "--heads", "origin", request.expected_branch],
        cwd=worktree_path,
    )
    return code == 0 and not output.strip()


def _issue_worktree_publish_pr_url(
    request: IssueWorktreePublishInspectionRequest, worktree_path: Path
) -> tuple[str | None, str | None]:
    command = [
        "gh",
        "pr",
        "create",
        "--repo",
        request.repository,
        "--base",
        request.base_branch,
        "--head",
        request.expected_branch,
        "--title",
        request.pr_title,
        "--body",
        f"Automated Runner publish task from issue #{request.source_issue}.",
    ]
    if request.draft_pr:
        command.append("--draft")
    code, output = run_command(command, cwd=worktree_path)
    if code != 0:
        return None, "create_pr_failed"
    pr_url = output.strip()
    if _PUBLIC_GITHUB_PR_URL_RE.fullmatch(pr_url) is None:
        return None, "create_pr_failed"
    return pr_url, None


_CONTAINER_VALIDATION_PUBLISH_ALLOWED_METADATA_FIELDS = frozenset(
    (
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Source Issue",
        "Expected Source Branch",
        "Base Branch",
        "Output Branch",
        "Draft PR",
        "Operator Approval",
    )
)


def _container_validation_publish_metadata(
    body: str,
) -> tuple[ContainerValidationWorktreePublishRequest | None, str | None]:
    metadata = _metadata_before_task(body)
    fields = _metadata_field_names(metadata)
    if not fields <= _CONTAINER_VALIDATION_PUBLISH_ALLOWED_METADATA_FIELDS:
        return None, "unsupported_metadata_field"

    mode = _body_field(metadata, "Mode")
    maintenance_task_id = _body_field(metadata, "Maintenance Task ID")
    repository = _body_field(metadata, "Repository")
    source_issue = _body_field(metadata, "Source Issue")
    expected_source_branch = _body_field(metadata, "Expected Source Branch")
    base_branch = _body_field(metadata, "Base Branch")
    output_branch = _body_field(metadata, "Output Branch")
    draft_pr, draft_pr_reason = _issue_publish_bool_field(metadata, "Draft PR")
    operator_approval = _body_field(metadata, "Operator Approval")

    if mode != RUNTIME_MAINTENANCE_MODE:
        return None, "invalid_mode"
    if maintenance_task_id != PUBLISH_CONTAINER_VALIDATION_WORKTREE:
        return None, "unsupported_maintenance_task_id"
    if repository != REPO:
        return None, "unsupported_repository"
    if source_issue != str(CONTAINER_VALIDATION_SOURCE_ISSUE):
        return None, "unsupported_source_issue"
    if expected_source_branch != CONTAINER_VALIDATION_BRANCH:
        return None, "unsupported_expected_source_branch"
    if base_branch != CONTAINER_VALIDATION_BASE_BRANCH:
        return None, "unsupported_base_branch"
    if output_branch != CONTAINER_VALIDATION_BRANCH:
        return None, "unsupported_output_branch"
    if draft_pr_reason is not None:
        return None, draft_pr_reason
    if draft_pr is not True:
        return None, "draft_pr_required"
    if operator_approval != PUBLISH_CONTAINER_VALIDATION_WORKTREE:
        return None, "missing_operator_approval"

    return (
        ContainerValidationWorktreePublishRequest(
            repository=repository,
            source_issue=CONTAINER_VALIDATION_SOURCE_ISSUE,
            expected_source_branch=CONTAINER_VALIDATION_BRANCH,
            base_branch=CONTAINER_VALIDATION_BASE_BRANCH,
            output_branch=CONTAINER_VALIDATION_BRANCH,
            draft_pr=True,
            operator_approval=operator_approval,
        ),
        None,
    )


def _container_validation_registered_project() -> dict[str, Any]:
    project_tree = load_runner_project_tree()
    projects = project_tree.get("projects") if isinstance(project_tree, dict) else None
    project = projects.get("skeleton") if isinstance(projects, dict) else None
    if not isinstance(project, dict):
        raise ValueError("registered Skeleton project is missing")
    if project.get("repo") != REPO or project.get("public") is not True:
        raise ValueError("registered Skeleton project metadata mismatch")
    if not isinstance(project.get("worktree_root"), str):
        raise ValueError("registered Skeleton worktree root is missing")
    return project


def _container_validation_worktree_path() -> Path:
    project = _container_validation_registered_project()
    return Path(project["worktree_root"]) / CONTAINER_VALIDATION_WORKTREE_ID


def _ensure_safe_container_validation_worktree_path(path: Path) -> Path:
    project = _container_validation_registered_project()
    root = Path(project["worktree_root"]).resolve(strict=False)
    candidate = path.resolve(strict=False)
    if candidate.name != CONTAINER_VALIDATION_WORKTREE_ID:
        raise ValueError("container validation worktree id mismatch")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("container validation worktree outside registered root") from exc
    return candidate


def _safe_container_validation_status_path(path: str) -> bool:
    relative_path = Path(path)
    return (
        path == path.strip()
        and path != ""
        and not relative_path.is_absolute()
        and ".." not in relative_path.parts
        and "\\" not in path
    )


def _container_validation_existing_prs(
    request: ContainerValidationWorktreePublishRequest, worktree_path: Path
) -> list[dict[str, Any]]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            request.repository,
            "--head",
            request.output_branch,
            "--state",
            "open",
            "--json",
            (
                "number,state,isDraft,baseRefName,headRefName,headRefOid,"
                "headRepository,headRepositoryOwner,url"
            ),
        ],
        cwd=worktree_path,
    )
    if code != 0:
        raise RuntimeError("gh pr list failed")
    parsed = json.loads(output or "[]")
    if not isinstance(parsed, list) or not all(
        isinstance(item, dict) for item in parsed
    ):
        raise RuntimeError("gh pr list returned malformed JSON")
    return parsed


def _container_validation_pr_block_reason(
    request: ContainerValidationWorktreePublishRequest,
    pr_state: dict[str, Any],
    pushed_head_sha: str,
) -> str | None:
    if str(pr_state.get("state") or "").upper() != "OPEN":
        return "existing_pr_not_open"
    if pr_state.get("isDraft") is not True:
        return "existing_pr_not_draft"
    if pr_state.get("baseRefName") != request.base_branch:
        return "existing_pr_base_mismatch"
    if _head_repository_name_with_owner(pr_state) != request.repository:
        return "existing_pr_head_repository_mismatch"
    if pr_state.get("headRefName") != request.output_branch:
        return "existing_pr_head_branch_mismatch"
    if str(pr_state.get("headRefOid") or "").lower() != pushed_head_sha:
        return "existing_pr_head_sha_mismatch"
    if _existing_pr_publish_pr_url(pr_state) is None:
        return "existing_pr_url_unavailable"
    return None


def _container_validation_create_pr_url(
    request: ContainerValidationWorktreePublishRequest, worktree_path: Path
) -> tuple[str | None, str | None]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            request.repository,
            "--base",
            request.base_branch,
            "--head",
            request.output_branch,
            "--title",
            "Publish container package validation workflow",
            "--body",
            "Automated Runner publish task for retained issue #1667.",
            "--draft",
        ],
        cwd=worktree_path,
    )
    if code != 0:
        return None, "create_pr_failed"
    pr_url = output.strip()
    if _PUBLIC_GITHUB_PR_URL_RE.fullmatch(pr_url) is None:
        return None, "create_pr_failed"
    return pr_url, None


def publish_container_validation_worktree(body: str) -> str:
    task_id = PUBLISH_CONTAINER_VALIDATION_WORKTREE
    request, reason = _container_validation_publish_metadata(body)
    if reason is not None:
        return _maintenance_report("NEEDS_OPERATOR", task_id, [f"reason={reason}"], "not_met")
    assert request is not None

    status_lines = [
        f"repository={request.repository}",
        f"source_issue={request.source_issue}",
        f"expected_source_branch={request.expected_source_branch}",
        f"base_branch={request.base_branch}",
        f"expected_branch={request.output_branch}",
        f"draft_pr={str(request.draft_pr).lower()}",
        f"allowed_files_count={len(CONTAINER_VALIDATION_PUBLISH_FILES)}",
    ]
    try:
        worktree_path = _ensure_safe_container_validation_worktree_path(
            _container_validation_worktree_path()
        )
    except (KeyError, ValueError):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=issue_worktree_path_unsafe"],
            "not_met",
        )

    status_lines.append(f"issue_worktree_id={CONTAINER_VALIDATION_WORKTREE_ID}")
    if not worktree_path.exists():
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=issue_worktree_missing"], "not_met"
        )
    if not (worktree_path / ".git").exists():
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=issue_worktree_git_missing"], "not_met"
        )
    missing_files = [
        path for path in CONTAINER_VALIDATION_PUBLISH_FILES if not (worktree_path / path).exists()
    ]
    if missing_files:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"missing_file={missing_files[0]}", "reason=static_file_missing"],
            "not_met",
        )

    code, output = run_command(["git", "branch", "--show-current"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_current_branch status=failed"], "not_met"
        )
    current_branch_lines = _git_status_path_lines(output)
    current_branch = current_branch_lines[0] if current_branch_lines else ""
    status_lines.extend(("step=read_current_branch status=done", f"current_branch={current_branch}"))
    if current_branch != request.expected_source_branch:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=source_branch_mismatch"], "not_met"
        )

    code, output = run_command(["git", "remote", "get-url", "origin"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_origin_remote status=failed"], "not_met"
        )
    if not _remote_url_matches_project_repo(output, request.repository):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=origin_remote_mismatch"], "not_met"
        )

    code, output = run_command(["git", "diff", "--name-only", "HEAD", "--"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_changed_tracked_files status=failed"], "not_met"
        )
    changed_tracked_files = _git_status_path_lines(output)
    if not all(_safe_container_validation_status_path(path) for path in changed_tracked_files):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=changed_tracked_file_path_unsafe"], "not_met"
        )

    code, output = run_command(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree_path
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_untracked_files status=failed"], "not_met"
        )
    untracked_files = _git_status_path_lines(output)
    if not all(_safe_container_validation_status_path(path) for path in untracked_files):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=untracked_file_path_unsafe"], "not_met"
        )
    static_files = set(CONTAINER_VALIDATION_PUBLISH_FILES)
    unexpected_tracked_files = [
        path for path in changed_tracked_files if path not in static_files
    ]
    unexpected_untracked_files = [
        path
        for path in untracked_files
        if not _is_ignored_issue_publish_untracked_path(path)
        and path not in static_files
    ]
    if unexpected_tracked_files:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=changed_files_outside_static_set"], "not_met"
        )
    if unexpected_untracked_files:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=unexpected_untracked_files"], "not_met"
        )
    allowed_untracked_files = [
        path
        for path in untracked_files
        if not _is_ignored_issue_publish_untracked_path(path)
        and path in static_files
    ]
    validated_publish_files = [*changed_tracked_files, *allowed_untracked_files]
    status_lines.extend(
        (
            f"changed_tracked_files_count={len(changed_tracked_files)}",
            f"allowed_untracked_files_count={len(allowed_untracked_files)}",
            f"validated_publish_files_count={len(validated_publish_files)}",
        )
    )

    code, _output = run_command(
        [
            "git",
            "diff",
            "--quiet",
            f"{request.base_branch}...HEAD",
            "--",
            *CONTAINER_VALIDATION_PUBLISH_FILES,
        ],
        cwd=worktree_path,
    )
    if code == 0 and not allowed_untracked_files:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=no_publishable_changes"], "not_met"
        )
    if code not in {0, 1}:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_branch_diff status=failed"], "not_met"
        )

    code, _output = run_command(
        ["git", "diff", "--check", "--", *CONTAINER_VALIDATION_PUBLISH_FILES],
        cwd=worktree_path,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=diff_check_failed"], "not_met"
        )
    code, _output = run_command(
        ["git", "add", "--", *CONTAINER_VALIDATION_PUBLISH_FILES], cwd=worktree_path
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=staging_failed"], "not_met"
        )
    code, _output = run_command(
        ["git", "commit", "-m", "Publish container package validation workflow"],
        cwd=worktree_path,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=commit_failed"], "not_met"
        )
    code, output = run_command(["git", "rev-parse", "HEAD"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_post_commit_head status=failed"], "not_met"
        )
    pushed_head_lines = _git_status_path_lines(output)
    pushed_head_sha = pushed_head_lines[0].lower() if pushed_head_lines else ""
    if _HEAD_SHA_RE.fullmatch(pushed_head_sha) is None:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=post_commit_head_invalid"], "not_met"
        )
    status_lines.append(f"pushed_head_sha={pushed_head_sha}")

    push_ref = f"refs/heads/{request.output_branch}:refs/heads/{request.output_branch}"
    code, _output = run_command(["git", "push", "origin", push_ref], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=push_failed"], "not_met"
        )

    try:
        existing_prs = _container_validation_existing_prs(request, worktree_path)
    except (RuntimeError, json.JSONDecodeError):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_existing_pr status=failed"], "not_met"
        )
    if len(existing_prs) > 1:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=multiple_existing_prs"], "not_met"
        )
    if len(existing_prs) == 1:
        pr_state = existing_prs[0]
        pr_reason = _container_validation_pr_block_reason(
            request, pr_state, pushed_head_sha
        )
        if pr_reason is not None:
            return _maintenance_report(
                "BLOCKED", task_id, [*status_lines, f"reason={pr_reason}"], "not_met"
            )
        pr_url = _existing_pr_publish_pr_url(pr_state)
        assert pr_url is not None
        status_lines.extend(
            (
                "step=read_existing_pr status=done",
                f"post_push_pr_changed_files_count={len(CONTAINER_VALIDATION_PUBLISH_FILES)}",
                f"pr_url={pr_url}",
            )
        )
        return _maintenance_report("DONE", task_id, status_lines, "met")

    pr_url, pr_reason = _container_validation_create_pr_url(request, worktree_path)
    if pr_reason is not None:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, f"step=create_draft_pr status=failed reason={pr_reason}"], "not_met"
        )
    assert pr_url is not None
    status_lines.extend(
        (
            "step=read_existing_pr status=done",
            "step=create_draft_pr status=done",
            f"post_push_pr_changed_files_count={len(CONTAINER_VALIDATION_PUBLISH_FILES)}",
            f"pr_url={pr_url}",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _existing_pr_publish_pr_state(
    request: IssueWorktreeExistingPrPublishRequest, worktree_path: Path
) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "view",
            str(request.pr_number),
            "--repo",
            request.repository,
            "--json",
            (
                "number,state,isDraft,baseRefName,headRefName,headRefOid,"
                "headRepository,headRepositoryOwner,url,files"
            ),
        ],
        cwd=worktree_path,
    )
    if code != 0:
        raise RuntimeError("gh pr view failed")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh pr view returned non-object JSON")
    return parsed


def _existing_pr_publish_file_paths(pr_state: dict[str, Any]) -> frozenset[str]:
    files = pr_state.get("files")
    if not isinstance(files, list):
        return frozenset()
    paths: set[str] = set()
    for file_info in files:
        if not isinstance(file_info, dict):
            continue
        path = file_info.get("path")
        if not isinstance(path, str):
            path = file_info.get("filename")
        if isinstance(path, str):
            paths.add(path)
    return frozenset(paths)


def _existing_pr_publish_pr_url(pr_state: dict[str, Any]) -> str | None:
    url = pr_state.get("url")
    if isinstance(url, str) and _PUBLIC_GITHUB_PR_URL_RE.fullmatch(url) is not None:
        return url
    return None


def _existing_pr_publish_block_reason(
    request: IssueWorktreeExistingPrPublishRequest, pr_state: dict[str, Any]
) -> str | None:
    if pr_state.get("number") != request.pr_number:
        return "pr_number_mismatch"
    if str(pr_state.get("state") or "").upper() != "OPEN":
        return "pr_not_open"
    if pr_state.get("isDraft") is not True:
        return "pr_not_draft"
    if pr_state.get("baseRefName") != "main":
        return "pr_base_mismatch"
    if _head_repository_name_with_owner(pr_state) != request.repository:
        return "pr_head_repository_mismatch"
    if pr_state.get("headRefName") != request.expected_pr_head_branch:
        return "pr_head_branch_mismatch"
    head_sha = str(pr_state.get("headRefOid") or "").lower()
    if head_sha != request.expected_pr_head_sha:
        return "pr_head_sha_mismatch"
    if _existing_pr_publish_pr_url(pr_state) is None:
        return "pr_url_unavailable"
    return None


def _existing_pr_publish_post_push_block_reason(
    request: IssueWorktreeExistingPrPublishRequest,
    pr_state: dict[str, Any],
    pushed_head_sha: str,
) -> str | None:
    if pr_state.get("number") != request.pr_number:
        return "post_push_pr_number_mismatch"
    if str(pr_state.get("state") or "").upper() != "OPEN":
        return "post_push_pr_not_open"
    if pr_state.get("isDraft") is not True:
        return "post_push_pr_not_draft"
    if pr_state.get("baseRefName") != "main":
        return "post_push_pr_base_mismatch"
    if _head_repository_name_with_owner(pr_state) != request.repository:
        return "post_push_pr_head_repository_mismatch"
    if pr_state.get("headRefName") != request.expected_pr_head_branch:
        return "post_push_pr_head_branch_mismatch"
    head_sha = str(pr_state.get("headRefOid") or "").lower()
    if head_sha != pushed_head_sha:
        return "post_push_pr_head_sha_mismatch"
    if _existing_pr_publish_pr_url(pr_state) is None:
        return "post_push_pr_url_unavailable"
    return None


def _registered_worktree_overlay_metadata(
    body: str,
) -> tuple[RegisteredWorktreeOverlayRequest | None, str | None]:
    metadata = _metadata_before_task(body)
    fields = _metadata_field_names(metadata)
    if fields != _REGISTERED_WORKTREE_OVERLAY_ALLOWED_METADATA_FIELDS:
        return None, "unsupported_metadata_field"
    if _body_field(metadata, "Mode") != RUNTIME_MAINTENANCE_MODE:
        return None, "invalid_mode"
    if _body_field(metadata, "Maintenance Task ID") != OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR:
        return None, "unsupported_maintenance_task_id"
    packet_id = _body_field(metadata, "Recovery Packet")
    packet = REGISTERED_WORKTREE_OVERLAY_PACKETS.get(packet_id or "")
    if packet is None:
        return None, "unknown_recovery_packet"
    operator_approval = _body_field(metadata, "Operator Approval")
    if operator_approval != OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR:
        return None, "missing_operator_approval"
    return RegisteredWorktreeOverlayRequest(packet=packet, operator_approval=operator_approval), None


def _registered_skeleton_worktree_root() -> Path:
    project_tree = load_runner_project_tree()
    projects = project_tree.get("projects") if isinstance(project_tree, dict) else None
    project = projects.get("skeleton") if isinstance(projects, dict) else None
    if not isinstance(project, dict):
        raise ValueError("registered Skeleton project is missing")
    if project.get("repo") != REPO or project.get("public") is not True:
        raise ValueError("registered Skeleton project metadata mismatch")
    if not isinstance(project.get("worktree_root"), str):
        raise ValueError("registered Skeleton worktree root is missing")
    return Path(project["worktree_root"])


def _registered_worktree_overlay_source_path(
    packet: RegisteredWorktreeOverlayPacket,
) -> Path:
    root = _registered_skeleton_worktree_root().resolve(strict=False)
    candidate = (root / f"issue-{packet.source_issue}").resolve(strict=False)
    if candidate == root or candidate.name != f"issue-{packet.source_issue}":
        raise ValueError("registered source worktree path mismatch")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("registered source worktree outside root") from exc
    return candidate


def _git_with_temporary_index(command: list[str], cwd: Path, index_path: Path) -> tuple[int, str]:
    environment = dict(os.environ)
    environment["GIT_INDEX_FILE"] = str(index_path)
    token = _RUN_COMMAND_ENV_OVERRIDE.set(environment)
    try:
        return run_command(command, cwd=cwd)
    finally:
        _RUN_COMMAND_ENV_OVERRIDE.reset(token)


def _git_changed_files_between(
    base_sha: str, head_sha: str, cwd: Path
) -> tuple[int, frozenset[str]]:
    code, output = run_command(
        ["git", "diff", "--name-only", base_sha, head_sha, "--"], cwd=cwd
    )
    if code != 0:
        return code, frozenset()
    return 0, frozenset(_git_status_path_lines(output))


def _registered_worktree_overlay_pr_state(
    request: IssueWorktreeExistingPrPublishRequest, worktree_path: Path
) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "view",
            str(request.pr_number),
            "--repo",
            request.repository,
            "--json",
            (
                "number,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,"
                "headRepository,headRepositoryOwner,url,files"
            ),
        ],
        cwd=worktree_path,
    )
    if code != 0:
        raise RuntimeError("gh pr view failed")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh pr view returned non-object JSON")
    return parsed


def _registered_worktree_overlay_pr_state_shape_valid(
    pr_state: dict[str, Any], *, strict_values: bool = False
) -> bool:
    if not isinstance(pr_state.get("number"), int):
        return False
    if str(pr_state.get("state") or "").upper() not in {"OPEN", "CLOSED", "MERGED"}:
        return False
    if not isinstance(pr_state.get("isDraft"), bool):
        return False
    for field in ("baseRefName", "baseRefOid", "headRefName", "headRefOid", "url"):
        if not isinstance(pr_state.get(field), str):
            return False
    if strict_values:
        if _HEAD_SHA_RE.fullmatch(pr_state["baseRefOid"]) is None:
            return False
        if _HEAD_SHA_RE.fullmatch(pr_state["headRefOid"]) is None:
            return False
    if _head_repository_name_with_owner(pr_state) is None:
        return False
    if strict_values and _existing_pr_publish_pr_url(pr_state) is None:
        return False
    files = pr_state.get("files")
    if not isinstance(files, list):
        return False
    for file_info in files:
        if not isinstance(file_info, dict):
            return False
        path = file_info.get("path")
        if not isinstance(path, str):
            path = file_info.get("filename")
        if not isinstance(path, str):
            return False
        if strict_values and not _safe_issue_publish_file_path(path):
            return False
    return True


class _RegisteredOverlayNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _registered_worktree_overlay_rest_allowed(packet: RegisteredWorktreeOverlayPacket) -> bool:
    if packet.packet_id not in REGISTERED_WORKTREE_OVERLAY_PACKETS:
        return False
    if REGISTERED_WORKTREE_OVERLAY_PACKETS[packet.packet_id] != packet:
        return False
    if REPO != _REGISTERED_OVERLAY_PUBLIC_REST_REPO:
        return False
    project_tree = load_runner_project_tree()
    projects = project_tree.get("projects") if isinstance(project_tree, dict) else None
    project = projects.get("skeleton") if isinstance(projects, dict) else None
    return isinstance(project, dict) and project.get("repo") == REPO and project.get("public") is True


def _registered_worktree_overlay_rest_path(pr_number: int, *, page: int | None = None) -> str:
    path = f"/repos/{_REGISTERED_OVERLAY_PUBLIC_REST_REPO}/pulls/{pr_number}"
    if page is not None:
        path = f"{path}/files?per_page=100&page={page}"
    return path


def _registered_worktree_overlay_rest_url_allowed(url: str, pr_number: int) -> bool:
    parsed = urllib.parse.urlparse(url)
    path_prefix = _registered_worktree_overlay_rest_path(pr_number)
    files_prefix = f"{path_prefix}/files"
    if parsed.scheme != "https" or parsed.hostname != _REGISTERED_OVERLAY_PUBLIC_REST_HOST:
        return False
    if parsed.path == path_prefix and parsed.query == "":
        return True
    if parsed.path != files_prefix:
        return False
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    return set(query) == {"per_page", "page"} and query["per_page"] == ["100"] and len(query["page"]) == 1 and re.fullmatch(r"[1-9]\d*", query["page"][0]) is not None


def _registered_worktree_overlay_fetch_public_rest_json(
    path: str, pr_number: int, byte_cap: int
) -> tuple[object, Mapping[str, str]]:
    url = f"https://{_REGISTERED_OVERLAY_PUBLIC_REST_HOST}{path}"
    if not _registered_worktree_overlay_rest_url_allowed(url, pr_number):
        raise RuntimeError("registered overlay REST path outside boundary")
    request = urllib.request.Request(url, headers=_REGISTERED_OVERLAY_PUBLIC_REST_HEADERS)
    opener = urllib.request.build_opener(_RegisteredOverlayNoRedirectHandler)
    try:
        with opener.open(
            request, timeout=_REGISTERED_OVERLAY_PUBLIC_REST_TIMEOUT_SECONDS
        ) as response:
            final_url = response.geturl()
            if not _registered_worktree_overlay_rest_url_allowed(final_url, pr_number):
                raise RuntimeError("registered overlay REST final URL outside boundary")
            status = getattr(response, "status", response.getcode())
            if not isinstance(status, int) or status < 200 or status >= 300:
                raise RuntimeError("registered overlay REST non-2xx response")
            data = response.read(byte_cap + 1)
            if len(data) > byte_cap:
                raise RuntimeError("registered overlay REST response too large")
            try:
                parsed = json.loads(data.decode("utf-8") or "null")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("registered overlay REST malformed JSON") from exc
            return parsed, response.headers
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError("registered overlay REST request failed") from exc


def _registered_worktree_overlay_rest_repository_name(repository: object) -> str | None:
    if not isinstance(repository, dict):
        return None
    full_name = repository.get("full_name")
    if isinstance(full_name, str) and full_name:
        return full_name
    owner = repository.get("owner")
    name = repository.get("name")
    login = owner.get("login") if isinstance(owner, dict) else None
    if isinstance(login, str) and isinstance(name, str) and login and name:
        return f"{login}/{name}"
    return None


def _registered_worktree_overlay_files_from_rest(pr_number: int) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for page in range(1, _REGISTERED_OVERLAY_PUBLIC_REST_FILE_PAGE_CAP + 1):
        payload, headers = _registered_worktree_overlay_fetch_public_rest_json(
            _registered_worktree_overlay_rest_path(pr_number, page=page),
            pr_number,
            _REGISTERED_OVERLAY_PUBLIC_REST_FILES_BYTES,
        )
        if not isinstance(payload, list):
            raise RuntimeError("registered overlay REST files response malformed")
        for item in payload:
            if not isinstance(item, dict):
                raise RuntimeError("registered overlay REST file item malformed")
            filename = item.get("filename")
            if not isinstance(filename, str) or not _safe_issue_publish_file_path(filename):
                raise RuntimeError("registered overlay REST file path malformed")
            if filename in seen_paths:
                raise RuntimeError("registered overlay REST duplicate file path")
            seen_paths.add(filename)
            files.append({"path": filename})
            if len(files) > _REGISTERED_OVERLAY_PUBLIC_REST_FILE_COUNT_CAP:
                raise RuntimeError("registered overlay REST file count exceeded")
        link = headers.get("Link", "")
        if len(payload) < 100:
            if 'rel="next"' in link:
                raise RuntimeError("registered overlay REST inconsistent pagination")
            return files
        if 'rel="next"' not in link:
            raise RuntimeError("registered overlay REST missing pagination data")
    raise RuntimeError("registered overlay REST pagination exceeded")


def _registered_worktree_overlay_public_rest_pr_state(
    request: IssueWorktreeExistingPrPublishRequest,
    packet: RegisteredWorktreeOverlayPacket,
) -> dict[str, Any]:
    if not _registered_worktree_overlay_rest_allowed(packet):
        raise RuntimeError("registered overlay REST fallback not allowed")
    if request.repository != _REGISTERED_OVERLAY_PUBLIC_REST_REPO:
        raise RuntimeError("registered overlay REST repository mismatch")
    if request.pr_number != packet.pr_number:
        raise RuntimeError("registered overlay REST PR number mismatch")
    pr_payload, _headers = _registered_worktree_overlay_fetch_public_rest_json(
        _registered_worktree_overlay_rest_path(request.pr_number),
        request.pr_number,
        _REGISTERED_OVERLAY_PUBLIC_REST_PR_BYTES,
    )
    if not isinstance(pr_payload, dict):
        raise RuntimeError("registered overlay REST PR response malformed")
    base = pr_payload.get("base")
    head = pr_payload.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise RuntimeError("registered overlay REST PR response missing refs")
    head_repo = head.get("repo")
    head_repo_name = _registered_worktree_overlay_rest_repository_name(head_repo)
    base_sha = base.get("sha")
    head_sha = head.get("sha")
    base_ref = base.get("ref")
    head_ref = head.get("ref")
    html_url = pr_payload.get("html_url")
    state = pr_payload.get("state")
    number = pr_payload.get("number")
    draft = pr_payload.get("draft")
    if number != request.pr_number:
        raise RuntimeError("registered overlay REST PR number mismatch")
    if state not in {"open", "closed"}:
        raise RuntimeError("registered overlay REST PR state malformed")
    if not isinstance(draft, bool):
        raise RuntimeError("registered overlay REST PR draft malformed")
    if not isinstance(base_ref, str) or not isinstance(head_ref, str):
        raise RuntimeError("registered overlay REST PR branch malformed")
    if not isinstance(base_sha, str) or _HEAD_SHA_RE.fullmatch(base_sha) is None:
        raise RuntimeError("registered overlay REST base SHA malformed")
    if not isinstance(head_sha, str) or _HEAD_SHA_RE.fullmatch(head_sha) is None:
        raise RuntimeError("registered overlay REST head SHA malformed")
    if head_repo_name != request.repository:
        raise RuntimeError("registered overlay REST head repository mismatch")
    if not isinstance(html_url, str) or _PUBLIC_GITHUB_PR_URL_RE.fullmatch(html_url) is None:
        raise RuntimeError("registered overlay REST PR URL malformed")
    files = _registered_worktree_overlay_files_from_rest(request.pr_number)
    owner, name = request.repository.split("/", 1)
    pr_state = {
        "number": number,
        "state": state.upper(),
        "isDraft": draft,
        "baseRefName": base_ref,
        "baseRefOid": base_sha.lower(),
        "headRefName": head_ref,
        "headRefOid": head_sha.lower(),
        "headRepository": {
            "nameWithOwner": request.repository,
            "owner": {"login": owner},
            "name": name,
        },
        "headRepositoryOwner": {"login": owner},
        "url": html_url,
        "files": files,
    }
    if not _registered_worktree_overlay_pr_state_shape_valid(pr_state, strict_values=True):
        raise RuntimeError("registered overlay REST normalized shape invalid")
    return pr_state


def _registered_worktree_overlay_pr_state_with_fallback(
    request: IssueWorktreeExistingPrPublishRequest,
    packet: RegisteredWorktreeOverlayPacket,
    worktree_path: Path,
) -> tuple[dict[str, Any], str]:
    try:
        pr_state = _registered_worktree_overlay_pr_state(request, worktree_path)
    except (RuntimeError, json.JSONDecodeError):
        return (
            _registered_worktree_overlay_public_rest_pr_state(request, packet),
            "public_rest",
        )
    if _registered_worktree_overlay_pr_state_shape_valid(pr_state):
        return pr_state, "gh"
    return (
        _registered_worktree_overlay_public_rest_pr_state(request, packet),
        "public_rest",
    )


def _registered_worktree_overlay_base_ref_oid(pr_state: dict[str, Any]) -> str | None:
    base_ref_oid = str(pr_state.get("baseRefOid") or "").lower()
    if _HEAD_SHA_RE.fullmatch(base_ref_oid) is None:
        return None
    return base_ref_oid


def _registered_worktree_overlay_post_push_block_reason(
    request: IssueWorktreeExistingPrPublishRequest,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    pushed_head_sha: str,
) -> str | None:
    reason = _existing_pr_publish_post_push_block_reason(
        request, after_state, pushed_head_sha
    )
    if reason is not None:
        return reason
    stable_fields = ("number", "state", "isDraft", "baseRefName", "baseRefOid", "headRefName")
    for field in stable_fields:
        if after_state.get(field) != before_state.get(field):
            return f"post_push_pr_{field}_changed"
    if _head_repository_name_with_owner(after_state) != _head_repository_name_with_owner(before_state):
        return "post_push_pr_head_repository_changed"
    if _registered_worktree_overlay_base_ref_oid(after_state) is None:
        return "post_push_pr_base_ref_oid_invalid"
    return None


def _registered_worktree_overlay_safe_source_file(
    packet: RegisteredWorktreeOverlayPacket,
    source_path: Path,
    relative_path: str,
) -> bool:
    if relative_path not in packet.allowed_files:
        return False
    if not _safe_issue_publish_file_path(relative_path):
        return False
    source_root = source_path.resolve(strict=False)
    candidate = source_path / relative_path
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(source_root)
    except ValueError:
        return False
    current = source_path
    for part in Path(relative_path).parts[:-1]:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return False
        except OSError:
            return False
    try:
        file_stat = candidate.lstat()
    except OSError:
        return False
    file_mode = file_stat.st_mode
    return (
        stat.S_ISREG(file_mode)
        and not stat.S_ISLNK(file_mode)
        and file_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) == 0
    )


def _registered_worktree_overlay_untracked_diff_check(
    source_path: Path, relative_path: str
) -> bool:
    code, output = run_command(
        [
            "git",
            "diff",
            "--check",
            "--no-index",
            "--",
            os.devnull,
            relative_path,
        ],
        cwd=source_path,
    )
    return code in {0, 1} and not output.strip()


def _registered_worktree_overlay_index_blob(
    source_path: Path, relative_path: str
) -> str | None:
    code, output = run_command(["git", "ls-files", "--stage", "--", relative_path], cwd=source_path)
    if code != 0:
        return None
    lines = _git_status_path_lines(output)
    if len(lines) != 1:
        return None
    parts = lines[0].split()
    if len(parts) < 4 or parts[0] != "100644" or parts[2] != "0":
        return None
    blob_sha = parts[1].lower()
    if _HEAD_SHA_RE.fullmatch(blob_sha) is None:
        return None
    return blob_sha


def _registered_worktree_overlay_source_specs(
    packet: RegisteredWorktreeOverlayPacket,
    source_path: Path,
    changed_tracked_files: tuple[str, ...],
    allowed_untracked_files: tuple[str, ...],
) -> tuple[tuple[RegisteredWorktreeOverlaySourceSpec, ...] | None, str | None]:
    specs: list[RegisteredWorktreeOverlaySourceSpec] = []
    for relative_path in changed_tracked_files:
        code, _output = run_command(["git", "diff", "--quiet", "--", relative_path], cwd=source_path)
        if code == 0:
            blob_sha = _registered_worktree_overlay_index_blob(source_path, relative_path)
            if blob_sha is None:
                return None, "unsafe_source_file"
            specs.append(RegisteredWorktreeOverlaySourceSpec(relative_path, blob_sha))
        elif code == 1:
            if not _registered_worktree_overlay_safe_source_file(
                packet, source_path, relative_path
            ):
                return None, "unsafe_source_file"
            specs.append(RegisteredWorktreeOverlaySourceSpec(relative_path))
        else:
            return None, "source_diff_status_failed"
    for relative_path in allowed_untracked_files:
        if not _registered_worktree_overlay_safe_source_file(
            packet, source_path, relative_path
        ):
            return None, "unsafe_source_file"
        if not _registered_worktree_overlay_untracked_diff_check(
            source_path, relative_path
        ):
            return None, "untracked_diff_check_failed"
        specs.append(RegisteredWorktreeOverlaySourceSpec(relative_path))
    return tuple(specs), None


def _registered_worktree_overlay_validate_tracked_whitespace(
    source_path: Path,
    specs: tuple[RegisteredWorktreeOverlaySourceSpec, ...],
) -> str | None:
    tracked_specs = tuple(spec for spec in specs if spec.path)
    if not tracked_specs:
        return None
    temp_index = tempfile.NamedTemporaryFile(prefix="runner-overlay-validate-index-", delete=False)
    temp_index_path = Path(temp_index.name)
    temp_index.close()
    try:
        temp_index_path.unlink(missing_ok=True)
        code, _output = _git_with_temporary_index(
            ["git", "read-tree", "HEAD"],
            source_path,
            temp_index_path,
        )
        if code != 0:
            return "temporary_index_failed"
        for spec in tracked_specs:
            if spec.blob_sha is None:
                code, _output = _git_with_temporary_index(
                    ["git", "update-index", "--add", "--", spec.path],
                    source_path,
                    temp_index_path,
                )
            else:
                code, _output = _git_with_temporary_index(
                    [
                        "git",
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        "100644",
                        spec.blob_sha,
                        spec.path,
                    ],
                    source_path,
                    temp_index_path,
                )
            if code != 0:
                return "temporary_index_failed"
        code, _output = _git_with_temporary_index(
            ["git", "diff", "--check", "--cached", "HEAD", "--", *(spec.path for spec in tracked_specs)],
            source_path,
            temp_index_path,
        )
        if code != 0:
            return "diff_check_failed"
        return None
    finally:
        temp_index_path.unlink(missing_ok=True)


def _overlay_registered_worktree_commit(
    packet: RegisteredWorktreeOverlayPacket,
    source_path: Path,
    specs: tuple[RegisteredWorktreeOverlaySourceSpec, ...],
) -> tuple[str | None, str | None]:
    temp_index = tempfile.NamedTemporaryFile(prefix="runner-overlay-index-", delete=False)
    temp_index_path = Path(temp_index.name)
    temp_index.close()
    try:
        temp_index_path.unlink(missing_ok=True)
        code, _output = _git_with_temporary_index(
            ["git", "read-tree", packet.target_head_sha],
            source_path,
            temp_index_path,
        )
        if code != 0:
            return None, "temporary_index_failed"
        for spec in specs:
            if spec.blob_sha is None:
                code, output = run_command(
                    [
                        "git",
                        "hash-object",
                        "-w",
                        f"--path={spec.path}",
                        str(source_path / spec.path),
                    ],
                    cwd=source_path,
                )
                if code != 0:
                    return None, "temporary_index_failed"
                blob_lines = _git_status_path_lines(output)
                blob_sha = blob_lines[0].lower() if blob_lines else ""
                if _HEAD_SHA_RE.fullmatch(blob_sha) is None:
                    return None, "temporary_index_failed"
            else:
                blob_sha = spec.blob_sha
            code, _output = _git_with_temporary_index(
                ["git", "update-index", "--add", "--cacheinfo", "100644", blob_sha, spec.path],
                source_path,
                temp_index_path,
            )
            if code != 0:
                return None, "temporary_index_failed"
        code, output = _git_with_temporary_index(
            ["git", "write-tree"], source_path, temp_index_path
        )
        if code != 0:
            return None, "temporary_index_failed"
        tree_lines = _git_status_path_lines(output)
        tree_sha = tree_lines[0].lower() if tree_lines else ""
        if _HEAD_SHA_RE.fullmatch(tree_sha) is None:
            return None, "tree_verification_failed"
        code, output = run_command(
            [
                "git",
                "commit-tree",
                tree_sha,
                "-p",
                packet.target_head_sha,
                "-m",
                f"Overlay registered worktree packet {packet.packet_id}",
            ],
            cwd=source_path,
        )
        if code != 0:
            return None, "commit_failed"
        commit_lines = _git_status_path_lines(output)
        commit_sha = commit_lines[0].lower() if commit_lines else ""
        if _HEAD_SHA_RE.fullmatch(commit_sha) is None:
            return None, "commit_verification_failed"
        return commit_sha, None
    finally:
        temp_index_path.unlink(missing_ok=True)


def overlay_registered_worktree_to_existing_pr(body: str) -> str:
    task_id = OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR
    request, reason = _registered_worktree_overlay_metadata(body)
    if reason is not None:
        return _maintenance_report("NEEDS_OPERATOR", task_id, [f"reason={reason}"], "not_met")
    assert request is not None
    packet = request.packet
    allowed_files = frozenset(packet.allowed_files)
    status_lines = [
        f"repository={REPO}",
        f"recovery_packet={packet.packet_id}",
        f"source_issue={packet.source_issue}",
        f"expected_source_branch={packet.source_branch}",
        f"pull_request={packet.pr_number}",
        f"expected_target_branch={packet.target_branch}",
        f"expected_target_head_sha={packet.target_head_sha}",
        f"allowed_files_count={len(packet.allowed_files)}",
    ]

    try:
        source_path = _registered_worktree_overlay_source_path(packet)
    except (KeyError, ValueError):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=issue_worktree_path_unsafe"], "not_met"
        )
    if not source_path.exists():
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=issue_worktree_missing"], "not_met"
        )
    if not (source_path / ".git").exists():
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=issue_worktree_git_missing"], "not_met"
        )

    existing_request = IssueWorktreeExistingPrPublishRequest(
        repository=REPO,
        source_issue=packet.source_issue,
        expected_source_branch=packet.source_branch,
        pr_number=packet.pr_number,
        expected_pr_head_sha=packet.target_head_sha,
        expected_pr_head_branch=packet.target_branch,
        allowed_files=allowed_files,
    )
    try:
        pr_state, pr_metadata_source = _registered_worktree_overlay_pr_state_with_fallback(
            existing_request, packet, source_path
        )
    except (RuntimeError, json.JSONDecodeError):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_pr_metadata status=failed"], "not_met"
        )
    pr_reason = _existing_pr_publish_block_reason(existing_request, pr_state)
    pre_push_pr_files = _existing_pr_publish_file_paths(pr_state)
    if pr_reason is not None:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, f"reason={pr_reason}"], "not_met")
    base_ref_oid = _registered_worktree_overlay_base_ref_oid(pr_state)
    if base_ref_oid is None:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=base_ref_oid_invalid"], "not_met")
    pr_url = _existing_pr_publish_pr_url(pr_state)
    assert pr_url is not None
    status_lines.extend(
        (
            "step=read_pr_metadata status=done",
            f"pr_metadata_source={pr_metadata_source}",
            f"pre_push_pr_file_count={len(pre_push_pr_files)}",
            f"base_ref_oid={base_ref_oid}",
            f"pr_url={pr_url}",
        )
    )

    code, output = run_command(["git", "branch", "--show-current"], cwd=source_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_current_branch status=failed"], "not_met"
        )
    current_branch_lines = _git_status_path_lines(output)
    current_branch = current_branch_lines[0] if current_branch_lines else ""
    status_lines.append(f"current_branch={current_branch}")
    if current_branch != packet.source_branch:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=source_branch_mismatch"], "not_met")

    code, output = run_command(["git", "remote", "get-url", "origin"], cwd=source_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_origin_remote status=failed"], "not_met"
        )
    if not _remote_url_matches_project_repo(output, REPO):
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=origin_remote_mismatch"], "not_met")

    code, output = run_command(["git", "diff", "--name-only", "HEAD", "--"], cwd=source_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_changed_tracked_files status=failed"], "not_met"
        )
    changed_tracked_files = _git_status_path_lines(output)
    if not all(_safe_issue_publish_file_path(path) for path in changed_tracked_files):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=changed_tracked_file_path_unsafe"], "not_met"
        )
    code, output = run_command(["git", "ls-files", "--others", "--exclude-standard"], cwd=source_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_untracked_files status=failed"], "not_met"
        )
    untracked_files = _git_status_path_lines(output)
    if not all(_safe_issue_publish_file_path(path) or _is_ignored_issue_publish_untracked_path(path) for path in untracked_files):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=untracked_file_path_unsafe"], "not_met"
        )
    unexpected_untracked_files = [
        path for path in untracked_files if not _is_ignored_issue_publish_untracked_path(path) and path not in allowed_files
    ]
    allowed_untracked_files = [
        path for path in untracked_files if not _is_ignored_issue_publish_untracked_path(path) and path in allowed_files
    ]
    if unexpected_untracked_files:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=unexpected_untracked_files"], "not_met")
    validated_files = tuple(dict.fromkeys([*changed_tracked_files, *allowed_untracked_files]))
    if not set(validated_files) <= allowed_files:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=changed_files_outside_allowlist"], "not_met")
    if not validated_files:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=no_publishable_changes"], "not_met")
    status_lines.extend(
        (
            f"changed_tracked_files_count={len(changed_tracked_files)}",
            f"allowed_untracked_files_count={len(allowed_untracked_files)}",
            f"validated_publish_files_count={len(validated_files)}",
        )
    )

    specs, source_file_reason = _registered_worktree_overlay_source_specs(
        packet,
        source_path,
        tuple(changed_tracked_files),
        tuple(allowed_untracked_files),
    )
    if source_file_reason is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"reason={source_file_reason}"],
            "not_met",
        )
    assert specs is not None
    whitespace_reason = _registered_worktree_overlay_validate_tracked_whitespace(
        source_path, specs
    )
    if whitespace_reason is not None:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, f"reason={whitespace_reason}"], "not_met"
        )

    code, _output = run_command(["git", "cat-file", "-e", f"{base_ref_oid}^{{commit}}"], cwd=source_path)
    if code != 0:
        code, _output = run_command(
            ["git", "fetch", "origin", "main:refs/remotes/origin/main"],
            cwd=source_path,
        )
        if code != 0:
            return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=base_object_unavailable"], "not_met")
        code, output = run_command(["git", "rev-parse", "refs/remotes/origin/main"], cwd=source_path)
        fetched_lines = _git_status_path_lines(output) if code == 0 else []
        fetched_sha = fetched_lines[0].lower() if fetched_lines else ""
        if fetched_sha != base_ref_oid:
            return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=fetched_base_sha_mismatch"], "not_met")

    code, _output = run_command(["git", "cat-file", "-e", f"{packet.target_head_sha}^{{commit}}"], cwd=source_path)
    if code != 0:
        code, _output = run_command(
            ["git", "fetch", "origin", f"{packet.target_branch}:refs/remotes/origin/{packet.target_branch}"],
            cwd=source_path,
        )
        if code != 0:
            return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=target_object_unavailable"], "not_met")
        code, output = run_command(["git", "rev-parse", f"refs/remotes/origin/{packet.target_branch}"], cwd=source_path)
        fetched_lines = _git_status_path_lines(output) if code == 0 else []
        fetched_sha = fetched_lines[0].lower() if fetched_lines else ""
        if fetched_sha != packet.target_head_sha:
            return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=fetched_target_sha_mismatch"], "not_met")

    new_commit_sha, commit_reason = _overlay_registered_worktree_commit(packet, source_path, specs)
    if commit_reason is not None:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, f"reason={commit_reason}"], "not_met")
    assert new_commit_sha is not None
    status_lines.append(f"constructed_head_sha={new_commit_sha}")

    code, output = run_command(["git", "rev-parse", f"{new_commit_sha}^"], cwd=source_path)
    parent_lines = _git_status_path_lines(output) if code == 0 else []
    parent_sha = parent_lines[0].lower() if parent_lines else ""
    if parent_sha != packet.target_head_sha:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=commit_parent_verification_failed"], "not_met")
    code, diff_files = _git_changed_files_between(packet.target_head_sha, new_commit_sha, source_path)
    if code != 0:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=commit_diff_verification_failed"], "not_met")
    if not diff_files <= allowed_files:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=commit_diff_outside_allowlist"], "not_met")
    code, pr_diff_files = _git_changed_files_between(base_ref_oid, new_commit_sha, source_path)
    if code != 0:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=pr_diff_verification_failed"], "not_met")
    if not pre_push_pr_files <= pr_diff_files:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=pre_existing_pr_files_missing"], "not_met")

    code, _output = run_command(
        [
            "git",
            "push",
            "origin",
            f"--force-with-lease={packet.target_branch}:{packet.target_head_sha}",
            f"{new_commit_sha}:refs/heads/{packet.target_branch}",
        ],
        cwd=source_path,
    )
    if code != 0:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=push_failed"], "not_met")

    try:
        post_push_pr_state, post_push_pr_metadata_source = (
            _registered_worktree_overlay_pr_state_with_fallback(
                existing_request, packet, source_path
            )
        )
    except (RuntimeError, json.JSONDecodeError):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=post_push_read_pr_metadata status=failed"], "not_met"
        )
    post_reason = _registered_worktree_overlay_post_push_block_reason(existing_request, pr_state, post_push_pr_state, new_commit_sha)
    if post_reason is not None:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, f"reason={post_reason}"], "not_met")
    post_push_pr_files = _existing_pr_publish_file_paths(post_push_pr_state)
    if not pre_push_pr_files <= post_push_pr_files:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=pre_existing_pr_files_missing"], "not_met")
    newly_introduced_files = post_push_pr_files - pre_push_pr_files
    if not newly_introduced_files <= allowed_files:
        return _maintenance_report("BLOCKED", task_id, [*status_lines, "reason=new_pr_files_outside_allowlist"], "not_met")
    status_lines.extend(
        (
            "step=push_expected_pr_branch status=done",
            "step=post_push_read_pr_metadata status=done",
            f"post_push_pr_metadata_source={post_push_pr_metadata_source}",
            f"pushed_head_sha={new_commit_sha}",
            f"post_push_pr_changed_files_count={len(post_push_pr_files)}",
            f"new_pr_changed_files_count={len(newly_introduced_files)}",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def publish_issue_worktree_to_existing_pr(body: str) -> str:
    task_id = PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR
    request, reason = _existing_pr_publish_metadata(body)
    if reason is not None:
        return _maintenance_report("NEEDS_OPERATOR", task_id, [f"reason={reason}"], "not_met")
    assert request is not None

    status_lines = [
        f"repository={request.repository}",
        f"source_issue={request.source_issue}",
        f"expected_source_branch={request.expected_source_branch}",
        f"pull_request={request.pr_number}",
        f"expected_pr_head_branch={request.expected_pr_head_branch}",
        f"expected_pr_head_sha={request.expected_pr_head_sha}",
        f"allowed_files_count={len(request.allowed_files)}",
    ]
    try:
        worktree_path = _ensure_safe_issue_publish_worktree_path(
            _issue_publish_worktree_path(request.source_issue)
        )
    except ValueError:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=issue_worktree_path_unsafe"],
            "not_met",
        )

    if worktree_path.name != f"issue-{request.source_issue}":
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=issue_worktree_path_mismatch"],
            "not_met",
        )
    status_lines.append(f"issue_worktree_id=issue-{request.source_issue}")
    if not worktree_path.exists():
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=issue_worktree_missing"], "not_met"
        )
    if not (worktree_path / ".git").exists():
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=issue_worktree_git_missing"], "not_met"
        )

    try:
        pr_state = _existing_pr_publish_pr_state(request, worktree_path)
    except (RuntimeError, json.JSONDecodeError):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_pr_metadata status=failed"],
            "not_met",
        )
    pr_reason = _existing_pr_publish_block_reason(request, pr_state)
    pre_push_pr_files = _existing_pr_publish_file_paths(pr_state)
    if pr_reason is not None:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, f"reason={pr_reason}"], "not_met"
        )
    pr_url = _existing_pr_publish_pr_url(pr_state)
    assert pr_url is not None
    status_lines.extend(
        (
            "step=read_pr_metadata status=done",
            f"pre_push_pr_changed_files_count={len(pre_push_pr_files)}",
            f"pr_url={pr_url}",
        )
    )

    code, output = run_command(["git", "branch", "--show-current"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_current_branch status=failed"],
            "not_met",
        )
    current_branch_lines = _git_status_path_lines(output)
    current_branch = current_branch_lines[0] if current_branch_lines else ""
    status_lines.append(f"current_branch={current_branch}")
    if current_branch != request.expected_source_branch:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=source_branch_mismatch"], "not_met"
        )

    code, output = run_command(["git", "remote", "get-url", "origin"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_origin_remote status=failed"],
            "not_met",
        )
    if not _remote_url_matches_project_repo(output, request.repository):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=origin_remote_mismatch"], "not_met"
        )

    code, _output = run_command(
        ["git", "merge-base", "--is-ancestor", request.expected_pr_head_sha, "HEAD"],
        cwd=worktree_path,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=source_not_derived_from_expected_pr_head"],
            "not_met",
        )
    status_lines.append("step=verify_source_ancestor status=done")

    code, output = run_command(["git", "diff", "--name-only", "HEAD", "--"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_changed_tracked_files status=failed"],
            "not_met",
        )
    changed_tracked_files = _git_status_path_lines(output)
    if not all(_safe_issue_publish_file_path(path) for path in changed_tracked_files):
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=changed_tracked_file_path_unsafe"], "not_met"
        )

    code, output = run_command(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree_path
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_untracked_files status=failed"],
            "not_met",
        )
    untracked_files = _git_status_path_lines(output)
    unexpected_untracked_files = [
        path
        for path in untracked_files
        if not _is_ignored_issue_publish_untracked_path(path)
        and path not in request.allowed_files
    ]
    allowed_untracked_files = [
        path
        for path in untracked_files
        if not _is_ignored_issue_publish_untracked_path(path)
        and path in request.allowed_files
    ]
    if unexpected_untracked_files:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=unexpected_untracked_files"], "not_met"
        )
    validated_publish_files = [*changed_tracked_files, *allowed_untracked_files]
    if not set(validated_publish_files) <= set(request.allowed_files):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=changed_files_outside_allowlist"],
            "not_met",
        )
    if not validated_publish_files:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=no_publishable_changes"], "not_met"
        )
    status_lines.extend(
        (
            f"changed_tracked_files_count={len(changed_tracked_files)}",
            f"allowed_untracked_files_count={len(allowed_untracked_files)}",
            f"validated_publish_files_count={len(validated_publish_files)}",
        )
    )

    code, _output = run_command(
        ["git", "diff", "--check", "--", *validated_publish_files],
        cwd=worktree_path,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=diff_check_failed"], "not_met"
        )
    code, _output = run_command(
        ["git", "add", "--", *validated_publish_files], cwd=worktree_path
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=staging_failed"], "not_met"
        )
    code, _output = run_command(
        ["git", "commit", "-m", f"Publish issue #{request.source_issue} worktree to existing PR"],
        cwd=worktree_path,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=commit_failed"], "not_met"
        )
    code, output = run_command(["git", "rev-parse", "HEAD"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "step=read_post_commit_head status=failed"], "not_met"
        )
    pushed_head_lines = _git_status_path_lines(output)
    pushed_head_sha = pushed_head_lines[0].lower() if pushed_head_lines else ""
    if _HEAD_SHA_RE.fullmatch(pushed_head_sha) is None or pushed_head_sha == request.expected_pr_head_sha:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=post_commit_head_invalid_or_unchanged"],
            "not_met",
        )
    status_lines.append(f"pushed_head_sha={pushed_head_sha}")

    code, _output = run_command(
        [
            "git",
            "push",
            "origin",
            f"--force-with-lease={request.expected_pr_head_branch}:{request.expected_pr_head_sha}",
            f"HEAD:refs/heads/{request.expected_pr_head_branch}",
        ],
        cwd=worktree_path,
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, "reason=push_failed"], "not_met"
        )

    try:
        post_push_pr_state = _existing_pr_publish_pr_state(request, worktree_path)
    except (RuntimeError, json.JSONDecodeError):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=post_push_read_pr_metadata status=failed"],
            "not_met",
        )
    post_reason = _existing_pr_publish_post_push_block_reason(
        request, post_push_pr_state, pushed_head_sha
    )
    if post_reason is not None:
        return _maintenance_report(
            "BLOCKED", task_id, [*status_lines, f"reason={post_reason}"], "not_met"
        )
    post_push_pr_files = _existing_pr_publish_file_paths(post_push_pr_state)
    if not pre_push_pr_files <= post_push_pr_files:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=pre_existing_pr_files_missing"],
            "not_met",
        )
    newly_introduced_files = post_push_pr_files - pre_push_pr_files
    if not newly_introduced_files <= set(request.allowed_files):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=new_pr_files_outside_allowlist"],
            "not_met",
        )
    status_lines.extend(
        (
            "step=push_expected_pr_branch status=done",
            "step=post_push_read_pr_metadata status=done",
            f"post_push_pr_changed_files_count={len(post_push_pr_files)}",
            f"new_pr_changed_files_count={len(newly_introduced_files)}",
        )
    )
    return _maintenance_report("DONE", task_id, status_lines, "met")


def _issue_worktree_publish_validated_report(
    body: str,
    task_id: str,
    *,
    publish: bool,
    explicit_recovery_route: bool = False,
    target_project_route: bool = False,
) -> str:
    metadata = (body or "").split("```task", 1)[0]
    failure_status = (
        "NEEDS_OPERATOR"
        if explicit_recovery_route or target_project_route
        else "BLOCKED"
    )
    request, reason = _issue_worktree_publish_inspection_metadata(
        body,
        require_repository=publish,
        explicit_recovery_route=explicit_recovery_route,
        target_project_route=target_project_route,
    )
    if reason is not None:
        return _maintenance_report(
            failure_status, task_id, [f"reason={reason}"], "not_met"
        )
    assert request is not None

    status_lines = [
        f"target_project={request.target_project}",
        f"repository={request.repository}",
        f"source_issue={request.source_issue}",
        f"base_branch={request.base_branch}",
        f"expected_branch={request.expected_branch}",
        f"draft_pr={str(request.draft_pr).lower()}",
        f"allowed_files_count={len(request.allowed_files)}",
    ]
    if target_project_route:
        status_lines.append(
            f"target_project_route={request.target_project}:{request.repository}"
        )
    try:
        if target_project_route:
            worktree_path = _ensure_safe_target_project_issue_publish_worktree_path(
                request
            )
        else:
            worktree_path = _ensure_safe_issue_publish_worktree_path(
                _issue_publish_worktree_path(request.source_issue)
            )
    except ValueError:
        return _maintenance_report(
            failure_status,
            task_id,
            [*status_lines, "reason=issue_worktree_path_unsafe"],
            "not_met",
        )

    status_lines.append(f"issue_worktree_id=issue-{request.source_issue}")
    if not worktree_path.exists():
        return _maintenance_report(
            failure_status,
            task_id,
            [*status_lines, "reason=issue_worktree_missing"],
            "not_met",
        )
    if not (worktree_path / ".git").exists():
        return _maintenance_report(
            failure_status,
            task_id,
            [*status_lines, "reason=issue_worktree_git_missing"],
            "not_met",
        )

    code, output = run_command(["git", "branch", "--show-current"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_current_branch status=failed"],
            "not_met",
        )
    current_branch_lines = _git_status_path_lines(output)
    current_branch = current_branch_lines[0] if current_branch_lines else ""
    status_lines.extend(
        ("step=read_current_branch status=done", f"current_branch={current_branch}")
    )
    if current_branch != request.expected_branch:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=branch_mismatch"],
            "not_met",
        )

    code, output = run_command(["git", "remote", "get-url", "origin"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_origin_remote status=failed"],
            "not_met",
        )
    if not _remote_url_matches_project_repo(output, request.repository):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=verify_origin_remote status=failed"],
            "not_met",
        )
    status_lines.extend(
        ("step=read_origin_remote status=done", "step=verify_origin_remote status=done")
    )

    code, output = run_command(["git", "diff", "--name-only", "HEAD", "--"], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_changed_tracked_files status=failed"],
            "not_met",
        )
    changed_tracked_files = _git_status_path_lines(output)
    if not all(_safe_issue_publish_file_path(path) for path in changed_tracked_files):
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [
                *status_lines,
                "step=read_changed_tracked_files status=done",
                "reason=changed_tracked_file_path_unsafe",
            ],
            "not_met",
        )
    status_lines.extend(
        (
            "step=read_changed_tracked_files status=done",
            f"changed_tracked_files_count={len(changed_tracked_files)}",
            f"changed_tracked_files={','.join(changed_tracked_files) if changed_tracked_files else '(none)'}",
        )
    )

    code, output = run_command(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree_path
    )
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=read_untracked_files status=failed"],
            "not_met",
        )
    untracked_files = _git_status_path_lines(output)
    unexpected_untracked_files = [
        path
        for path in untracked_files
        if not _is_ignored_issue_publish_untracked_path(path)
        and path not in request.allowed_files
    ]
    allowed_untracked_files = [
        path
        for path in untracked_files
        if not _is_ignored_issue_publish_untracked_path(path)
        and path in request.allowed_files
    ]
    status_lines.extend(
        (
            "step=read_untracked_files status=done",
            f"unexpected_untracked_files_count={len(unexpected_untracked_files)}",
            "unexpected_untracked_files="
            f"{json.dumps(unexpected_untracked_files, sort_keys=True)}",
            f"allowed_untracked_files_count={len(allowed_untracked_files)}",
            "allowed_untracked_files="
            f"{json.dumps(allowed_untracked_files, sort_keys=True)}",
        )
    )
    if unexpected_untracked_files:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=unexpected_untracked_files"],
            "not_met",
        )

    validated_publish_files = [*changed_tracked_files, *allowed_untracked_files]
    changed_files_allowed = set(validated_publish_files) <= set(request.allowed_files)
    status_lines.append(
        f"tracked_files_match_allowlist={str(changed_files_allowed).lower()}"
    )
    if not changed_files_allowed:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=changed_tracked_files_outside_allowlist"],
            "not_met",
        )
    status_lines.extend(
        (
            f"validated_publish_files_count={len(validated_publish_files)}",
            "validated_publish_files="
            f"{','.join(validated_publish_files) if validated_publish_files else '(none)'}",
        )
    )

    if not publish:
        return _maintenance_report("DONE", task_id, status_lines, "met")

    existing_pr_lookup = _issue_worktree_publish_existing_pr_url(request, worktree_path)
    status_lines.append(f"existing_pr_lookup={existing_pr_lookup.reason}")
    if existing_pr_lookup.reason == "existing_pr_lookup_unavailable":
        if not explicit_recovery_route:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    "step=read_existing_pr status=failed "
                    "reason=existing_pr_lookup_unavailable",
                ],
                "not_met",
            )
        override_reason, override_hash = _issue_worktree_publish_override_result(
            metadata, request
        )
        if override_reason != "publish_override_valid":
            return _maintenance_report(
                failure_status,
                task_id,
                [
                    *status_lines,
                    f"step=publish_override status=failed reason={override_reason}",
                    "reason=existing_pr_lookup_unavailable",
                ],
                "not_met",
            )
        status_lines.append(
            "step=publish_override status=done reason=publish_override_valid"
        )
        assert override_hash is not None
        status_lines.append(f"publish_override_hash={override_hash}")
        if not _issue_worktree_publish_remote_branch_absent(request, worktree_path):
            return _maintenance_report(
                failure_status,
                task_id,
                [
                    *status_lines,
                    "step=verify_remote_branch_absent status=failed "
                    "reason=remote_branch_conflict",
                    f"approved_override_hash={override_hash}",
                ],
                "not_met",
            )
        status_lines.append("step=verify_remote_branch_absent status=done")
    elif existing_pr_lookup.reason == "existing_pr_found":
        assert existing_pr_lookup.pr_url is not None
        return _maintenance_report(
            "DONE",
            task_id,
            [
                *status_lines,
                "step=read_existing_pr status=done",
                f"existing_pr_url={existing_pr_lookup.pr_url}",
            ],
            "met",
        )
    else:
        status_lines.append("step=read_existing_pr status=done")
        if (
            explicit_recovery_route
            and _body_field(metadata, "Publish Override") is not None
        ):
            status_lines.append(
                "step=publish_override status=skipped reason=existing_pr_not_found"
            )
        if existing_pr_lookup.reason != "existing_pr_not_found":
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    "step=read_existing_pr status=failed "
                    "reason=existing_pr_lookup_unavailable",
                ],
                "not_met",
            )

    if validated_publish_files:
        code, _output = run_command(
            ["git", "diff", "--check", "--", *validated_publish_files],
            cwd=worktree_path,
        )
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    "step=diff_check status=failed reason=diff_check_failed",
                ],
                "not_met",
            )
        status_lines.append("step=diff_check status=done")

        code, output = run_command(["git", "rev-parse", "HEAD"], cwd=worktree_path)
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [*status_lines, "step=read_pre_commit_head status=failed"],
                "not_met",
            )
        pre_commit_head_lines = _git_status_path_lines(output)
        pre_commit_head = pre_commit_head_lines[0] if pre_commit_head_lines else ""

        code, _output = run_command(
            ["git", "add", "--", *validated_publish_files], cwd=worktree_path
        )
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    "step=stage_validated_files status=failed reason=staging_failed",
                ],
                "not_met",
            )
        status_lines.append("step=stage_validated_files status=done")

        code, _output = run_command(
            [
                "git",
                "commit",
                "-m",
                (
                    f"Publish target project issue #{request.source_issue} worktree"
                    if request.target_project_route
                    else f"Publish issue #{request.source_issue} worktree"
                ),
            ],
            cwd=worktree_path,
        )
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    "step=commit_validated_files status=failed reason=commit_failed",
                ],
                "not_met",
            )
        status_lines.append("step=commit_validated_files status=done")

        code, output = run_command(["git", "rev-parse", "HEAD"], cwd=worktree_path)
        if code != 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [*status_lines, "step=read_post_commit_head status=failed"],
                "not_met",
            )
        post_commit_head_lines = _git_status_path_lines(output)
        post_commit_head = post_commit_head_lines[0] if post_commit_head_lines else ""
        if (
            not pre_commit_head
            or not post_commit_head
            or post_commit_head == pre_commit_head
        ):
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [
                    *status_lines,
                    "step=verify_commit_head_moved status=failed "
                    "reason=branch_head_did_not_move",
                ],
                "not_met",
            )
        status_lines.append("step=verify_commit_head_moved status=done")
    else:
        code, _output = run_command(
            ["git", "diff", "--quiet", f"{request.base_branch}...HEAD", "--"],
            cwd=worktree_path,
        )
        if code == 0:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [*status_lines, "reason=no_publishable_changes"],
                "not_met",
            )
        if code != 1:
            return _maintenance_report(
                "BLOCKED",
                task_id,
                [*status_lines, "step=read_branch_diff status=failed"],
                "not_met",
            )
        status_lines.append("step=read_branch_diff status=done")

    push_ref = f"refs/heads/{request.expected_branch}:refs/heads/{request.expected_branch}"
    code, _output = run_command(["git", "push", "origin", push_ref], cwd=worktree_path)
    if code != 0:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "step=push_expected_branch status=failed"],
            "not_met",
        )
    status_lines.append("step=push_expected_branch status=done")

    pr_url, pr_reason = _issue_worktree_publish_pr_url(request, worktree_path)
    if pr_reason is not None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, f"step=create_draft_pr status=failed reason={pr_reason}"],
            "not_met",
        )
    assert pr_url is not None
    status_lines.extend(("step=create_draft_pr status=done", f"draft_pr_url={pr_url}"))
    return _maintenance_report("DONE", task_id, status_lines, "met")


def inspect_issue_worktree_for_publish(body: str) -> str:
    return _issue_worktree_publish_validated_report(
        body, INSPECT_ISSUE_WORKTREE_FOR_PUBLISH, publish=False
    )


def publish_issue_worktree_pr(body: str) -> str:
    return _issue_worktree_publish_validated_report(
        body, PUBLISH_ISSUE_WORKTREE_PR, publish=True
    )


def publish_existing_issue_worktree(body: str) -> str:
    return _issue_worktree_publish_validated_report(
        body,
        PUBLISH_EXISTING_ISSUE_WORKTREE,
        publish=True,
        explicit_recovery_route=True,
    )


def publish_target_project_issue_worktree_pr(body: str) -> str:
    return _issue_worktree_publish_validated_report(
        body,
        PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR,
        publish=True,
        target_project_route=True,
    )


def home_edge_01_read_only_diagnostic() -> str:
    task_id = HOME_EDGE_01_READ_ONLY_DIAGNOSTIC
    from core.home_edge.diagnostics import compact_status, run_audited_home_edge_command

    artifact = run_audited_home_edge_command(
        "diagnostic",
        artifact_path=Path(tempfile.gettempdir())
        / "skeleton-home-edge"
        / "home-edge-01-diagnostic.latest.json",
    )
    compact = compact_status(artifact)
    summary = artifact.get("summary") if isinstance(artifact, dict) else None
    gateway = summary.get("gateway") if isinstance(summary, dict) else None
    modem = summary.get("modem") if isinstance(summary, dict) else None
    registered = modem.get("registered_expectation") if isinstance(modem, dict) else None
    status_lines = [
        "inventory_schema=home_edge_diagnostic_v2",
        "report_mode=read_only_lightweight",
        f"node_id={compact['node']}",
        f"gateway_status={gateway.get('status', 'unverified') if isinstance(gateway, dict) else 'unverified'}",
        f"route_status={compact['route_status']}",
        f"tailscale_status={compact['tailscale_status']}",
        f"modem_status={compact['modem_status']}",
        "usb_modem_health_requirement=false",
        f"internet_path_expectation={registered.get('internet_path', 'default_gateway') if isinstance(registered, dict) else 'default_gateway'}",
        f"gateway_modem_internals={registered.get('gateway_modem_internals', 'not_observed_by_home_edge') if isinstance(registered, dict) else 'not_observed_by_home_edge'}",
        "next_operator_action=none_for_healthy_transport",
        "diagnostic_count=1",
    ]
    success = (
        isinstance(gateway, dict)
        and gateway.get("status") == "ready"
        and compact["route_status"] == "unchanged"
        and compact["tailscale_status"] == "healthy"
    )
    return _maintenance_report(
        "DONE" if success else "NEEDS_OPERATOR",
        task_id,
        status_lines,
        "met" if success else "not_met",
    )


def home_edge_01_lan_inventory_read_only() -> str:
    task_id = HOME_EDGE_01_LAN_INVENTORY_READ_ONLY
    from core.home_edge.diagnostics import run_audited_home_edge_command

    result = run_audited_home_edge_command(
        "lan_inventory",
        artifact_path=Path(tempfile.gettempdir())
        / "skeleton-home-edge"
        / "home-edge-01-lan-inventory.private.json",
    )
    aggregate = result.get("aggregate") if isinstance(result, dict) else None
    data = aggregate if isinstance(aggregate, dict) else {}
    category_counts = data.get("service_category_counts")
    categories = category_counts if isinstance(category_counts, dict) else {}
    risk_flags = data.get("risk_flags")
    risks = risk_flags if isinstance(risk_flags, list) else ["invalid_aggregate"]
    category_value = ",".join(
        f"{key}:{categories[key]}" for key in sorted(categories)
    ) or "none"
    risk_value = ",".join(sorted(str(item) for item in risks)) or "none"
    status_lines = [
        "inventory_schema=home_edge_lan_inventory_v1",
        "report_mode=explicit_read_only",
        f"node_id={result.get('node_id', 'home-edge-01') if isinstance(result, dict) else 'home-edge-01'}",
        f"device_count={data.get('device_count', 0)}",
        f"responsive_count={data.get('responsive_count', 0)}",
        f"gateway_presence={data.get('gateway_presence', 'unverified')}",
        f"service_category_counts={category_value}",
        f"risk_flags={risk_value}",
        "private_details=private_runtime_artifact_only",
        "diagnostic_count=1",
    ]
    observed = isinstance(result, dict) and result.get("status") == "observed"
    return _maintenance_report(
        "DONE" if observed else "NEEDS_OPERATOR",
        task_id,
        status_lines,
        "met" if observed else "not_met",
    )


def _hermes_memory_gateway_smoke_packet(
    operation: str,
    parameters: dict[str, object],
    *,
    namespace: str = HERMES_MEMORY_GATEWAY_SMOKE_NAMESPACE,
    project_id: str = HERMES_MEMORY_GATEWAY_SMOKE_PROJECT_ID,
    task_id: str = "hermes-memory-gateway-smoke",
) -> dict[str, object]:
    return _executor_hermes_memory_gateway_smoke_packet(
        operation,
        parameters,
        namespace=namespace,
        project_id=project_id,
        task_id=task_id,
    )


def _hermes_memory_gateway_smoke_report_lines() -> list[str]:
    return _executor_hermes_memory_gateway_smoke_report_lines()


def _hermes_memory_gateway_smoke_failure(task_id: str, token: str) -> str:
    return _executor_hermes_memory_gateway_smoke_failure(
        task_id, token, maintenance_report=_maintenance_report
    )


def _hermes_memory_gateway_smoke_validate_common(
    result: object,
    *,
    operation: str,
    expected_status: str,
) -> tuple[dict[str, object] | None, str | None]:
    return _executor_hermes_memory_gateway_smoke_validate_common(
        result,
        operation=operation,
        expected_status=expected_status,
    )


def _hermes_memory_gateway_smoke_validate_isolation(
    result: object,
    *,
    expected_reason: str,
) -> str | None:
    return _executor_hermes_memory_gateway_smoke_validate_isolation(
        result,
        expected_reason=expected_reason,
    )


def _hermes_memory_gateway_smoke_bounded_count(value: object) -> bool:
    return _executor_hermes_memory_gateway_smoke_bounded_count(value)


def _hermes_memory_gateway_smoke_bounded_ref(value: object) -> bool:
    return _executor_hermes_memory_gateway_smoke_bounded_ref(value)


def _hermes_memory_gateway_smoke_validate_payload(
    result: dict[str, object], operation: str
) -> str | None:
    return _executor_hermes_memory_gateway_smoke_validate_payload(result, operation)


def _hermes_memory_gateway_smoke_exact_summary(
    result: dict[str, object],
) -> dict[str, object]:
    return _executor_hermes_memory_gateway_smoke_exact_summary(result)


def _hermes_memory_gateway_smoke_proposal(
    exact_summary: dict[str, object],
) -> dict[str, object]:
    return _executor_hermes_memory_gateway_smoke_proposal(exact_summary)


def _hermes_memory_gateway_smoke_run_and_validate(
    gateway: MemoryGateway,
    operation: str,
    parameters: dict[str, object],
    *,
    expected_status: str = "DRY_RUN_OK",
) -> tuple[dict[str, object] | None, str | None]:
    return _executor_hermes_memory_gateway_smoke_run_and_validate(
        gateway,
        operation,
        parameters,
        expected_status=expected_status,
        run_task_packet=run_hermes_memory_task_packet,
    )


def hermes_memory_gateway_smoke() -> str:
    return _execute_hermes_memory_gateway_smoke(
        task_id=HERMES_MEMORY_GATEWAY_SMOKE,
        maintenance_report=_maintenance_report,
        gateway_factory=MemoryGateway,
        capability_token_factory=capability_token,
        run_task_packet=run_hermes_memory_task_packet,
    )


def _mempalace_runtime_smoke_reject_issue_input(
    body: str,
) -> str | None:
    return _executor_reject_mempalace_runtime_smoke_issue_input(
        body,
        runtime_maintenance_mode=RUNTIME_MAINTENANCE_MODE,
        task_id=MEMPALACE_SYNTHETIC_RUNTIME_SMOKE,
    )


def _mempalace_runtime_smoke_preflight(
    task_id: str,
) -> tuple[RegisteredProjectCheckout | None, list[str], str | None]:
    registered_checkout, report = _registered_skeleton_checkout(task_id)
    if report is not None or registered_checkout is None:
        return None, [], report

    checkout_path = registered_checkout.checkout_path
    status_lines = list(registered_checkout.status_lines)
    present_report = _verify_skeleton_checkout_present(task_id, registered_checkout)
    if present_report is not None:
        return None, status_lines, present_report

    origin_report = _read_skeleton_origin(task_id, registered_checkout, status_lines)
    if origin_report is not None:
        return None, status_lines, origin_report

    branch_report = _read_skeleton_current_branch(task_id, checkout_path, status_lines)
    if branch_report is not None:
        return None, status_lines, branch_report

    clean_report = _read_skeleton_clean_state(task_id, checkout_path, status_lines)
    if clean_report is not None:
        return None, status_lines, clean_report

    fetch_report = _fetch_skeleton_origin_main(task_id, checkout_path, status_lines)
    if fetch_report is not None:
        return None, status_lines, fetch_report

    head_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "HEAD", status_lines, "read_checkout_head"
    )
    if failure_report is not None or head_sha is None:
        return None, status_lines, failure_report
    origin_main_sha, failure_report = _read_skeleton_sha(
        task_id, checkout_path, "origin/main", status_lines, "read_origin_main"
    )
    if failure_report is not None or origin_main_sha is None:
        return None, status_lines, failure_report
    if head_sha != origin_main_sha:
        return None, status_lines, _maintenance_report(
            "BLOCKED",
            task_id,
            [*status_lines, "reason=checkout_not_current_main"],
            "not_met",
        )
    return registered_checkout, status_lines, None


def _mempalace_benchmark_output_has_private_marker(
    output: str,
) -> bool:
    return _executor_mempalace_benchmark_output_has_private_marker(
        output,
        private_markers=MEMPALACE_SYNTHETIC_PRIVATE_MARKERS,
    )


def _parse_mempalace_benchmark_json(
    output: str,
) -> tuple[dict[str, object] | None, str | None]:
    return _executor_parse_mempalace_benchmark_json(output)


def _validate_mempalace_benchmark_report(
    report: dict[str, object],
) -> str | None:
    return _executor_validate_mempalace_benchmark_report(
        report,
        expected_schema=MEMPALACE_SYNTHETIC_BENCHMARK_SCHEMA,
        expected_namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
        expected_project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
        required_stable_reasons=(
            MEMPALACE_SYNTHETIC_REQUIRED_STABLE_REASONS
        ),
    )


def mempalace_synthetic_runtime_smoke(body: str) -> str:
    return _execute_mempalace_synthetic_runtime_smoke(
        body,
        task_id=MEMPALACE_SYNTHETIC_RUNTIME_SMOKE,
        reject_issue_input=(
            _mempalace_runtime_smoke_reject_issue_input
        ),
        preflight=_mempalace_runtime_smoke_preflight,
        output_has_private_marker=(
            _mempalace_benchmark_output_has_private_marker
        ),
        parse_benchmark_json=_parse_mempalace_benchmark_json,
        validate_benchmark_report=(
            _validate_mempalace_benchmark_report
        ),
        run_command=run_command,
        maintenance_report=_maintenance_report,
        timeout_seconds=(
            MEMPALACE_SYNTHETIC_BENCHMARK_TIMEOUT_SECONDS
        ),
    )


def dispatch_runtime_maintenance_task(
    task_id: str, workdir: str, body: str = ""
) -> str:
    if task_id not in RUNTIME_MAINTENANCE_TASK_IDS:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=maintenance_task_id_not_allowlisted"],
            "not_met",
        )
    try:
        if task_id == SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME:
            return sync_telegram_callback_poller_runtime(workdir)
        if task_id == ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG:
            return ensure_telegram_callback_local_config()
        if task_id == CHECK_SKELETON_FRESHNESS:
            return check_skeleton_freshness()
        if task_id == RUNTIME_SYNC_MAIN:
            return runtime_sync_main(body)
        if task_id == RECOVER_SKELETON_CHECKOUT:
            return recover_skeleton_checkout(body)
        if task_id == ENSURE_PROJECT_CHECKOUT:
            return ensure_project_checkout(body)
        if task_id == VALIDATE_PR_BRANCH:
            return validate_pr_branch(body)
        if task_id == PREFLIGHT_PR_REFRESH:
            return preflight_pr_refresh(body)
        if task_id == HERMES_WORKER_PREFLIGHT:
            return hermes_worker_preflight()
        if task_id == HERMES_MEMORY_GATEWAY_SMOKE:
            return hermes_memory_gateway_smoke()
        if task_id == LOOP_ENGINE_PACKET:
            return loop_engine_packet(body)
        if task_id == MEMPALACE_SYNTHETIC_RUNTIME_SMOKE:
            return mempalace_synthetic_runtime_smoke(body)
        if task_id == PRIVATE_MEMORY_HEALTHCHECK:
            return private_memory_healthcheck(body)
        if task_id == HERMES_PRIVATE_MEMORY_BRIDGE_CHECK:
            return hermes_private_memory_bridge_check()
        if task_id == INSTALL_GRAPHIFY_RUNTIME:
            return install_graphify_runtime(body)
        if task_id == PREPARE_AUFMASS_PRIVATE_RUNTIME:
            return prepare_aufmass_private_runtime()
        if task_id == RUN_AUFMASS_PRIVATE_DXF_REVIEW:
            return run_aufmass_private_dxf_review(body)
        if task_id == SUMMARIZE_AUFMASS_PRIVATE_REVIEW:
            return summarize_aufmass_private_review(body)
        if task_id == BUILD_AUFMASS_PRIVATE_SHORTLIST:
            return build_aufmass_private_shortlist(body)
        if task_id == BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE:
            return build_aufmass_private_area_schedule(body)
        if task_id == INSPECT_PR_MERGEABILITY:
            return inspect_pr_mergeability(body)
        if task_id == BACKFILL_SKELETON_MEMORY_RECENT:
            return backfill_skeleton_memory_recent()
        if task_id == INSPECT_ISSUE_WORKTREE_FOR_PUBLISH:
            return inspect_issue_worktree_for_publish(body)
        if task_id == PUBLISH_ISSUE_WORKTREE_PR:
            return publish_issue_worktree_pr(body)
        if task_id == PUBLISH_EXISTING_ISSUE_WORKTREE:
            return publish_existing_issue_worktree(body)
        if task_id == PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR:
            return publish_issue_worktree_to_existing_pr(body)
        if task_id == OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR:
            return overlay_registered_worktree_to_existing_pr(body)
        if task_id == PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR:
            return publish_target_project_issue_worktree_pr(body)
        if task_id == PUBLISH_CONTAINER_VALIDATION_WORKTREE:
            return publish_container_validation_worktree(body)
        if task_id == REPAIR_PROTECTED_EXACT_SOURCE_WORKTREE:
            return repair_protected_exact_source_worktree(body)
        if task_id == QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES:
            return quarantine_stale_clean_skeleton_worktrees(body)
        if task_id == HOME_EDGE_01_READ_ONLY_DIAGNOSTIC:
            return home_edge_01_read_only_diagnostic()
        if task_id == HOME_EDGE_01_LAN_INVENTORY_READ_ONLY:
            return home_edge_01_lan_inventory_read_only()
        return check_project_checkout(body)
    except Exception:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=maintenance_step_raised"],
            "not_met",
        )


def maintenance_report_is_done(report: str) -> bool:
    return maintenance_report_status(report) == "DONE" and not any(
        line.strip() == "success_criteria=not_met" for line in report.splitlines()
    )


def maintenance_report_status(report: str) -> str:
    text = _ANSI_ESCAPE_RE.sub("", _without_fenced_blocks(report or ""))
    lines = [line for line in text.splitlines() if line.strip()]
    matches: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _MAINTENANCE_TOP_LEVEL_STATUS_RE.match(line)
        if match is not None:
            matches.append((index, match.group(1).upper()))
    if len(matches) != 1:
        return "BLOCKED"
    index, status = matches[0]
    if index not in (0, len(lines) - 1):
        return "BLOCKED"
    return status


def get_pr_merge_state(pr_number: int) -> dict[str, Any]:
    code, output = run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            REPO,
            "--json",
            "number,state,isDraft,mergeable,headRefOid,comments",
        ]
    )
    if code != 0:
        raise RuntimeError(f"gh pr view failed:\n{output}")
    parsed = json.loads(output or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("gh pr view returned non-object JSON")
    return parsed


def telegram_approve_audit_matches_request(
    pr_state: dict[str, Any],
    request: TelegramApprovedPrMergeRequest,
) -> bool:
    comments = pr_state.get("comments")
    if not isinstance(comments, list):
        return False

    expected_lines = (
        "Operator event record (Telegram callback stage 1)",
        f"Pull request: #{request.pr_number}",
        "Action: telegram_approve",
        f"Head marker: {request.approved_head_sha[:8]}",
        f"Callback digest: {request.callback_digest}",
        "Result: recorded",
        "Verified approval record: signed_telegram_callback",
        f"Verified head SHA: {request.approved_head_sha}",
    )
    for comment in comments:
        body = comment.get("body") if isinstance(comment, dict) else None
        if isinstance(body, str) and all(line in body.splitlines() for line in expected_lines):
            return True
    return False


def telegram_approve_digest_is_signed(
    request: TelegramApprovedPrMergeRequest,
) -> bool:
    hmac_secret = os.environ.get(TELEGRAM_CALLBACK_HMAC_ENV)
    if not hmac_secret:
        return False
    digest = hmac.new(
        hmac_secret.encode("utf-8"),
        (
            f"tpr1:approve:p{request.pr_number}:"
            f"{request.approved_head_sha[:8]}"
        ).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()[:12]
    return hmac.compare_digest(request.callback_digest, digest)


def _pr_merge_block_reason(
    request: TelegramApprovedPrMergeRequest,
    pr_state: dict[str, Any],
) -> str | None:
    if not telegram_approve_digest_is_signed(request):
        return "Telegram approve callback HMAC signature is invalid."
    if pr_state.get("number") != request.pr_number:
        return "GitHub PR number does not match the approved merge request."
    if str(pr_state.get("state") or "").upper() != "OPEN":
        return "Approved PR is not open."
    if pr_state.get("isDraft") is not False:
        return "Approved PR is still draft."
    if str(pr_state.get("mergeable") or "").upper() != "MERGEABLE":
        return "Approved PR is not mergeable."
    if str(pr_state.get("headRefOid") or "").lower() != request.approved_head_sha:
        return "Approved PR head does not match the Telegram button head."
    if not telegram_approve_audit_matches_request(pr_state, request):
        return "Signed Telegram approve audit does not match this merge request."
    if request.action != TELEGRAM_APPROVED_PR_MERGE_ACTION:
        return "Approved PR merge action is not allowlisted."
    return None


def execute_telegram_approved_pr_merge(
    request: TelegramApprovedPrMergeRequest,
) -> str:
    pr_state = get_pr_merge_state(request.pr_number)
    block_reason = _pr_merge_block_reason(request, pr_state)
    if block_reason is not None:
        return f"BLOCKED: {block_reason}"

    command = [
        "gh",
        "pr",
        "merge",
        str(request.pr_number),
        "--repo",
        REPO,
        "--squash",
        "--match-head-commit",
        request.approved_head_sha,
    ]
    code, _output = run_command(command)
    if code != 0:
        return "BLOCKED: GitHub squash merge failed."
    return (
        f"DONE: Squash merged approved PR #{request.pr_number}.\n"
        f"approved_head_sha={request.approved_head_sha}\n"
        f"merge_action={request.action}"
    )


def process_telegram_approved_pr_merge_issue(
    issue_number: int,
    request: TelegramApprovedPrMergeRequest,
    memory_warning: str | None = None,
    retry_decision: RetryDecision | None = None,
) -> None:
    report = execute_telegram_approved_pr_merge(request)
    status = "DONE" if report.startswith("DONE:") else "BLOCKED"
    if status != "DONE" and retry_decision is not None:
        report = append_retry_fields(report, retry_decision)
    warning = record_runner_executor_result(
        issue_number,
        "skeleton",
        status,
        status,
        "maintenance",
        report,
    )
    report = append_memory_warning(report, warning or memory_warning)
    post_issue_comment(issue_number, report)
    set_issue_label(
        issue_number,
        LABEL_RUNNING,
        LABEL_DONE if status == "DONE" else LABEL_BLOCKED,
    )
    notify_task_finished(issue_number, status, report)


def process_runtime_maintenance_issue(
    issue_number: int,
    task_id: str,
    workdir: str,
    body: str = "",
    memory_warning: str | None = None,
    retry_decision: RetryDecision | None = None,
) -> None:
    report = dispatch_runtime_maintenance_task(task_id, workdir, body)
    status = maintenance_report_status(report)
    if status != "DONE" and retry_decision is not None:
        report = append_retry_fields(report, retry_decision)
    warning = record_runner_executor_result(
        issue_number,
        "skeleton",
        status,
        status,
        "maintenance",
        report,
    )
    report = append_memory_warning(report, warning or memory_warning)
    post_issue_comment(issue_number, report)
    set_issue_label(
        issue_number,
        LABEL_RUNNING,
        LABEL_DONE if status == "DONE" else LABEL_BLOCKED,
    )
    notify_task_finished(issue_number, status, report)


def process_issue(issue: dict[str, Any], workdir: str | None = None) -> None:
    issue_number = int(issue["number"])
    if not is_open_task_issue(issue):
        return

    coordinator_workdir = str(Path(workdir) if workdir is not None else DEFAULT_WORKDIR)
    issue_workdir: str | None = None
    claimed = False
    runner_task: RunnerTask | None = None
    retry_decision: RetryDecision | None = None
    try:
        issue_body = issue.get("body") or ""
        fence_reason = task_fence_block_reason(issue_body)
        if fence_reason is not None:
            block_issue(issue_number, fence_reason)
            return
        maintenance_mode, maintenance_task_id = extract_runtime_maintenance_task_id(
            issue_body
        )
        merge_mode, merge_request, merge_reason = (
            extract_telegram_approved_pr_merge_request(issue_body)
        )
        if maintenance_mode and maintenance_task_id is None:
            block_issue(
                issue_number,
                "Runtime maintenance task id is missing. Use `Maintenance Task ID:`.",
            )
            return
        if maintenance_mode and maintenance_task_id not in RUNTIME_MAINTENANCE_TASK_IDS:
            block_issue(
                issue_number,
                f"Runtime maintenance task id `{maintenance_task_id}` is not allowlisted.",
            )
            return
        if merge_mode and merge_request is None:
            block_issue(
                issue_number,
                merge_reason or "Telegram approved merge request is malformed.",
            )
            return
        route = runner_task_route(
            maintenance_mode=maintenance_mode,
            maintenance_task_id=maintenance_task_id,
            merge_mode=merge_mode,
        )

        if maintenance_mode:
            task_content = ""
        else:
            runner_task, task_reason = extract_runner_task(issue_body)
            if runner_task is None:
                if task_reason is not None:
                    block_issue(issue_number, task_reason)
                    return
                if merge_mode:
                    task_content = ""
                else:
                    block_issue(
                        issue_number,
                        "No fenced task block found. Add a fence that starts with ```task.",
                    )
                    return
            else:
                task_content = runner_task.content

        evaluate_runner_shadow_hook(
            issue_number=issue_number,
            issue_body=issue_body,
            route=route,
            maintenance_task_id=maintenance_task_id,
            runner_task=runner_task,
            merge_request=merge_request,
        )

        prior_comments = get_issue_comments(issue)
        retry_block_reason = "executor_invocation"
        if route == ROUTE_CODE_GENERATION:
            expected_output_reason = code_task_expected_output_block_reason(issue_body)
            if expected_output_reason is not None:
                retry_condition = retry_condition_for_issue(
                    issue_body,
                    route,
                    maintenance_task_id,
                    expected_output_reason,
                )
                if prior_comments is None:
                    first_decision = evaluate_retry_policy(retry_condition, [])
                    retry_decision = RetryDecision(
                        retry_decision=NEEDS_OPERATOR,
                        retry_attempt=1,
                        blocker_signature=first_decision.blocker_signature,
                        route=route,
                        next_required_action="DIAGNOSE",
                    )
                    report = unverifiable_retry_history_report(retry_decision)
                    post_issue_comment(issue_number, report)
                    set_issue_label(issue_number, LABEL_READY, LABEL_BLOCKED)
                    notify_task_finished(issue_number, "NEEDS_OPERATOR", report)
                    return
                retry_decision = evaluate_retry_policy(
                    retry_condition,
                    parse_prior_blocked_reports(
                        prior_comments, trusted_runner_comment_authors()
                    ),
                    extract_retry_override(issue_body),
                )
                block_issue(
                    issue_number,
                    f"{expected_output_reason}: expected_output is required before code execution.",
                    runner_task=runner_task,
                    retry_decision=retry_decision,
                )
                return
        retry_condition = retry_condition_for_issue(
            issue_body,
            route,
            maintenance_task_id,
            retry_block_reason,
        )
        if prior_comments is None:
            retry_decision = RetryDecision(
                retry_decision=NEEDS_OPERATOR,
                retry_attempt=1,
                blocker_signature=evaluate_retry_policy(retry_condition, []).blocker_signature,
                route=route,
                next_required_action="DIAGNOSE",
            )
            report = unverifiable_retry_history_report(retry_decision)
            post_issue_comment(issue_number, report)
            set_issue_label(issue_number, LABEL_READY, LABEL_BLOCKED)
            notify_task_finished(issue_number, "NEEDS_OPERATOR", report)
            return
        retry_decision = evaluate_retry_policy(
            retry_condition,
            parse_prior_blocked_reports(
                        prior_comments, trusted_runner_comment_authors()
                    ),
            extract_retry_override(issue_body),
        )
        if retry_decision.retry_decision in {BLOCK_REPEATED_REASON, NEEDS_OPERATOR}:
            report = repeated_blocker_report(retry_decision)
            warning = record_runner_executor_result(
                issue_number,
                runner_task.target_project if runner_task is not None else "skeleton",
                "NEEDS_OPERATOR",
                "NEEDS_OPERATOR",
                None,
                report,
            )
            post_issue_comment(issue_number, append_memory_warning(report, warning))
            set_issue_label(issue_number, LABEL_READY, LABEL_BLOCKED)
            notify_task_finished(issue_number, "NEEDS_OPERATOR", report)
            return

        if runner_task is not None:
            execution_block_reason = project_execution_block_reason(runner_task)
            if execution_block_reason is not None:
                block_issue(
                    issue_number,
                    execution_block_reason,
                    runner_task=runner_task,
                    retry_decision=retry_decision,
                )
                return
            if runner_task.target_repository != QUEUE_REPOSITORY:
                checkout_block_reason = verify_target_repository_checkout(
                    runner_task.target_repository
                )
                if checkout_block_reason is not None:
                    block_issue(
                        issue_number,
                        checkout_block_reason,
                        runner_task=runner_task,
                        retry_decision=retry_decision,
                    )
                    return

        apply_runner_lane_label(issue_number, runner_task)

        if maintenance_mode:
            clean, status_output = ensure_clean_worktree(coordinator_workdir)
            if not clean:
                block_issue(
                    issue_number,
                    "Runner worktree is not clean before starting.\n\n"
                    f"git status --short:\n```\n{status_output.strip()}\n```",
                    retry_decision=retry_decision,
                )
                return

        set_issue_label(issue_number, LABEL_READY, LABEL_RUNNING)
        claimed = True
        executor_name = "maintenance" if maintenance_mode or merge_mode else "codex"
        pickup_memory_warning = record_runner_task_picked_up(
            issue_number,
            runner_task.target_project if runner_task is not None else "skeleton",
            executor_name,
        )

        if maintenance_mode and maintenance_task_id is not None:
            if pickup_memory_warning:
                process_runtime_maintenance_issue(
                    issue_number,
                    maintenance_task_id,
                    coordinator_workdir,
                    issue_body,
                    pickup_memory_warning,
                    retry_decision,
                )
            else:
                process_runtime_maintenance_issue(
                    issue_number,
                    maintenance_task_id,
                    coordinator_workdir,
                    issue_body,
                    retry_decision=retry_decision,
                )
            return
        if merge_mode and merge_request is not None:
            if pickup_memory_warning:
                process_telegram_approved_pr_merge_issue(
                    issue_number, merge_request, pickup_memory_warning, retry_decision
                )
            else:
                process_telegram_approved_pr_merge_issue(
                    issue_number, merge_request, retry_decision=retry_decision
                )
            return

        target_repository = (
            runner_task.target_repository if runner_task is not None else QUEUE_REPOSITORY
        )
        local_target_worktree = target_repository != QUEUE_REPOSITORY
        if local_target_worktree:
            worktree_code, worktree_output, worktree_path = (
                prepare_target_repository_issue_worktree(
                    target_repository,
                    issue_number,
                )
            )
        else:
            worktree_code, worktree_output, worktree_path = prepare_issue_branch(
                issue_number, coordinator_workdir
            )
        if worktree_code != 0:
            block_issue(
                issue_number,
                "Issue worktree preparation failed:\n"
                f"```\n{worktree_output}\n```"
                + issue_workspace_review_note(worktree_path),
                remove_label=LABEL_RUNNING,
                runner_task=runner_task,
                retry_decision=retry_decision,
            )
            return
        issue_workdir = str(worktree_path)

        cleanup_runtime_artifacts(issue_workdir)
        codex_code, codex_output = run_codex_task(
            task_content, issue_workdir, runner_task
        )
        cleanup_runtime_artifacts(issue_workdir)
        codex_result = classify_codex_task_result(codex_output, codex_code)
        if codex_code != 0:
            block_issue(
                issue_number,
                f"Codex task failed:\n```\n{codex_output}\n```"
                + issue_workspace_review_note(issue_workdir),
                remove_label=LABEL_RUNNING,
                runner_task=runner_task,
                retry_decision=retry_decision,
            )
            return
        if codex_result.status == "BLOCKED":
            report = report_runner_lane(
                blocked_codex_output_report(
                    codex_output,
                    codex_result.marker or "BLOCKED",
                    issue_workdir,
                ),
                runner_task,
            )
            report = append_retry_fields(report, retry_decision)
            warning = record_runner_executor_result(
                issue_number,
                runner_task.target_project if runner_task is not None else "skeleton",
                "BLOCKED",
                "BLOCKED",
                "codex",
                report,
            )
            report = append_memory_warning(report, warning or pickup_memory_warning)
            post_issue_comment(issue_number, report)
            set_issue_label(issue_number, LABEL_RUNNING, LABEL_BLOCKED)
            notify_task_finished(issue_number, "BLOCKED", report)
            return

        if local_target_worktree and runner_task is not None:
            finalized_report = finalize_local_worktree_success(
                issue_workdir, codex_output, runner_task
            )
        else:
            finalized_report = finalize_success(issue, issue_workdir, codex_output)
        report = report_runner_lane(finalized_report, runner_task)
        cleanup_runtime_artifacts(issue_workdir)
        if local_target_worktree:
            cleanup_code, cleanup_output = cleanup_target_repository_issue_worktree(
                target_repository,
                issue_number,
            )
        else:
            cleanup_code, cleanup_output = cleanup_issue_worktree(
                issue_number, coordinator_workdir
            )
        if cleanup_code != 0:
            raise RuntimeError(
                "Issue workspace cleanup failed:\n"
                f"{cleanup_output.strip() or f'exit code {cleanup_code}'}"
            )
        status = runner_report_status(report)
        if status == "BLOCKED":
            report = blocked_final_report(report)
            report = append_retry_fields(report, retry_decision)
        warning = record_runner_executor_result(
            issue_number,
            runner_task.target_project if runner_task is not None else "skeleton",
            status,
            status,
            "codex",
            report,
        )
        report = append_memory_warning(report, warning or pickup_memory_warning)
        post_issue_comment(issue_number, report)
        set_issue_label(
            issue_number,
            LABEL_RUNNING,
            LABEL_DONE if status == "DONE" else LABEL_BLOCKED,
        )
        notify_task_finished(issue_number, status, report)
    except Exception as exc:
        if issue_workdir is not None:
            cleanup_runtime_artifacts(issue_workdir)
        try:
            remove_label = LABEL_RUNNING if claimed else LABEL_READY
            block_issue(
                issue_number,
                f"Runner error:\nReason: {type(exc).__name__}\n```\n{exc}\n```"
                + (
                    issue_workspace_review_note(issue_workdir)
                    if issue_workdir is not None
                    else ""
                ),
                remove_label=remove_label,
                runner_task=runner_task,
                result_status="ERROR",
                retry_decision=retry_decision,
            )
        except Exception:
            return


def poll_once(workdir: str | None = None) -> int:
    issues = get_ready_issues()
    for issue in issues:
        process_issue(issue, workdir=workdir)
    return len(issues)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll GitHub runner task issues.")
    parser.add_argument("--loop", action="store_true", help="poll continuously")
    parser.add_argument("--workdir", default=None, help="repository workdir")
    args = parser.parse_args()

    if args.loop:
        while True:
            poll_once(workdir=args.workdir)
            time.sleep(POLL_INTERVAL)
    else:
        poll_once(workdir=args.workdir)


if __name__ == "__main__":
    main()
