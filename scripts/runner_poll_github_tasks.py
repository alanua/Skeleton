from __future__ import annotations

import argparse
import base64
import binascii
from collections.abc import Callable, Mapping
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
import sqlite3
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
MODULE_ROOT = ROOT
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.audit_ledger import AuditLedger, validate_public_safe_payload
from core.aufmass_source_pack import validate_source_pack_manifest
from core.codex_runtime_recovery import (
    CodexRuntimeRecoveryError,
    pinned_codex_recovery_marker_present,
    pinned_codex_runtime_path,
)
from core.control_recovery import (
    CODEGEN_UNKNOWN_VARIANT_MAX_MESSAGE,
    CONTROL_RECOVERY_SCHEMA,
    FailureClass,
    RecoveryStore,
    derive_failure_fingerprint,
    execute_recovery_packet,
    is_codegen_unknown_variant_max_failure,
    production_control_recovery_db_path,
)
from core.hermes_private_memory import (
    orient_hermes_private_memory,
    record_hermes_private_memory_note,
    write_hermes_private_memory_heartbeat,
)
from core.home_edge.esp_lab_activation import (
    OPERATOR_APPROVAL_REF as HOME_EDGE_ESP_LAB_STAGE1_ACTIVATION_OPERATOR_APPROVAL,
    TASK_ID as HOME_EDGE_ESP_LAB_STAGE1_ACTIVATION_TASK_ID,
    activate_esp_lab_stage1,
)
from core.hermes_worker import run_hermes_memory_task_packet
from core.loop_controller import LoopPolicy
from core.loop_engine import LoopEngine
from core.loop_runner_adapter import run_loop_task_packet
from core.loop_state_store import LoopStateStore
from core.mail_gmail_production_canary import (
    GmailReadonlyCanaryError,
    MAIL_GMAIL_READONLY_CANARY_TASK_ID,
    allowed_gmail_canary_accounts,
    blocked_gmail_readonly_receipt,
    run_gmail_readonly_canary,
)
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
from core.runner_vnext_cutover_bridge import (
    VNEXT_MODE_AUTHORITATIVE,
    VNEXT_MODES,
    VNextCutoverBridgeError,
    evaluate_vnext_cutover_bridge,
)
from core.runner_vnext_authority import (
    ROUTE_CODE_GENERATION as VNEXT_ROUTE_CODE_GENERATION,
    RunnerVNextAuthorityError,
    RunnerVNextRuntimeConfig,
    authority_receipt_from_bound,
    bind_runner_operation,
    authoritative_route_inventory_proof,
    build_authoritative_stores,
    grant_green_authority,
    prepare_green_authority,
)
from core.runner_vnext_contracts import PrivacyClass
from core.runner_vnext_execution import GreenExecutionError, GreenExecutionLifecycle
from core.runner_vnext_live_codegen import (
    LiveCodegenAdapter,
    PollerMechanicalCodegenBackend,
    RunnerVNextLiveCodegenError,
)
from core.runner_vnext_leases import Lane
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot
from core.runner_vnext_queue import (
    RunnerVNextQueueItem,
    RunnerVNextQueueSource,
    registered_runner_queue_sources,
    runner_vnext_queue_item_priority_key,
)
from core.runner_shadow_integration import (
    MAINTENANCE_TASK_KIND_BY_ID as SHADOW_MAINTENANCE_TASK_KIND_BY_ID,
    RunnerShadowReceipt,
    RunnerShadowCompatibilityBindings,
    ShadowBlocked,
    blocked_shadow_receipt,
    evaluate_shadow_from_normalized_metadata,
    gate_context_from_metadata,
    registry_parity_reason_codes,
    runner_task_from_normalized_metadata,
    task_envelope_hash,
)
from core.runner_executor import CallableRunnerExecutor, RunnerExecutorError
from core.runner_executor_registry import RunnerExecutorRegistry
from core.runner_gate import ROUTE_REQUIRED_CAPABILITIES, RunnerGate
from core.runner_repository_maintenance_executor import (
    BUILD_AND_LOCAL_OTA_OPERATION,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALLER_BLOB,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID,
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL,
    LAVALAMP_APPROVAL_REFERENCE,
    LAVALAMP_ARTIFACT_ROOT,
    LAVALAMP_IDEMPOTENCY_KEY,
    LAVALAMP_PLATFORMIO_ENV,
    LAVALAMP_RELAY,
    LAVALAMP_REPOSITORY,
    LAVALAMP_REQUIRED_EFFECTS,
    LAVALAMP_SOURCE_BRANCH,
    LAVALAMP_SOURCE_SHA,
    LAVALAMP_TARGET,
    LAVALAMP_WLED_SHA,
    RepositoryMaintenanceExecutor,
    RegisteredMaintenanceExecutor,
    execute_home_edge_esp_lab_stage1_signer_install as _execute_home_edge_esp_lab_stage1_signer_install,
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
from core.runner_private_memory_source_inventory import (
    CANDIDATE_CATEGORIES as PRIVATE_MEMORY_INVENTORY_CATEGORIES,
    TASK_ID as PRIVATE_MEMORY_PHASE_A_INVENTORY,
    execute_private_memory_phase_a_inventory,
)
from core.runner_five_layer_memory_activation import (
    TASK_ID as ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY,
    execute_five_layer_memory_activation,
)
from core.merge_policy_checker import (
    AUTO_MERGE_ALLOWED,
    DelegatedMergePolicyChecker,
)
from core.private_static_site_runtime import (
    DEPLOY_TASK_ID as DEPLOY_PRIVATE_STATIC_SITE,
    PRIVATE_KEY_MARKERS,
    PREPARE_TASK_ID as PREPARE_PRIVATE_STATIC_SITE_HANDOFF,
    deploy_private_static_site as _execute_deploy_private_static_site,
    prepare_private_static_site_handoff as _execute_prepare_private_static_site_handoff,
)
from core.project_tree import get_project, get_project_by_repo, load_project_tree
from core.runner_child_environment import (
    CODEX_AUTH_MODE_OPENAI_API,
    prepare_primary_codex_environment,
    sanitize_codegen_child_environment,
    sanitize_validation_child_environment,
    selected_primary_codex_auth_mode,
)
from integrations.credential_runtime import RegisteredCredentialRuntimeError
from core.runner_process_observer import (
    build_spawn_trace_command,
    parse_first_denied_filesystem_event,
)
from core.secret_reference import (
    SecretReferenceRegistrationError,
    registered_bitwarden_reference_from_systemd_index,
)
from core.runner_codegen_router import (
    CodegenRouteError,
    codex_failure_allows_secondary,
    openhands_secondary_command,
    prepare_openhands_secondary_environment,
    select_openhands_secondary_route,
    task_contract_allows_cloud_secondary,
)
from core.runner_retry_policy import (
    ALLOW_FIRST_ATTEMPT,
    ALLOW_ONE_TIME_OVERRIDE,
    BLOCK_REPEATED_REASON,
    NEEDS_OPERATOR,
    PriorBlockedReport,
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
from core.runner_task import RunnerTask as CoreRunnerTask
from core.scheduler_engine import SchedulerEngine, production_scheduler_db_path
from core.scheduler_models import ScheduleSpec, build_execution_proposal, stable_occurrence_id
from core.scheduler_store import SchedulerStore
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE, SharedDispatcher
from core.memory_bootstrap import (
    MEMORY_BOOTSTRAP_REQUEST_SCHEMA,
    MemoryBootstrapError,
    MemoryBootstrap,
)
from core.memory_scope_resolver import MemoryScopeResolutionError, task_transition_hash
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
RUNNER_MODE_ENV = "SKELETON_RUNNER_MODE"
RUNNER_VNEXT_MODE_ENV = "SKELETON_RUNNER_VNEXT_MODE"
RUNNER_VNEXT_STATE_ROOT_ENV = "SKELETON_RUNNER_VNEXT_STATE_ROOT"
RUNNER_VNEXT_LEDGER_DB_ENV = "SKELETON_RUNNER_VNEXT_LEDGER_DB"
RUNNER_VNEXT_LEASE_DB_ENV = "SKELETON_RUNNER_VNEXT_LEASE_DB"
RUNNER_VNEXT_NODE_SNAPSHOT_JSON_ENV = "SKELETON_RUNNER_VNEXT_NODE_SNAPSHOT_JSON"
RUNNER_SHADOW_MODE_ENV = "SKELETON_RUNNER_SHADOW_MODE"
RUNNER_MODE_OFF = "off"
RUNNER_MODE_SHADOW = "shadow"
RUNNER_MODE_ENFORCE = "enforce"
RUNNER_MODES = frozenset((RUNNER_MODE_OFF, RUNNER_MODE_SHADOW, RUNNER_MODE_ENFORCE))
UNIVERSAL_RUNNER_ALLOWED_STATUS = "allowed"
LAST_RUNNER_SHADOW_RECEIPT: dict[str, object] | None = None
LAST_RUNNER_VNEXT_RECEIPT: dict[str, object] | None = None
_CURRENT_QUEUE_REPOSITORY: ContextVar[str] = ContextVar(
    "CURRENT_QUEUE_REPOSITORY", default=QUEUE_REPOSITORY
)
RUNNER_VNEXT_NODE_SNAPSHOT_SCHEMA = "skeleton.runner_vnext_node_capability_snapshot.v1"
RUNNER_VNEXT_CODEGEN_NODE_ID = "node:runner-vnext-codegen-runtime"
RUNNER_VNEXT_CODEGEN_ROUTE_RANK = 0
RUNNER_VNEXT_NODE_SNAPSHOT_MAX_TTL_SECONDS = 60.0
RUNNER_VNEXT_READONLY_PREFLIGHT_TASK_ID = "runner_vnext_readonly_preflight"
RUNNER_VNEXT_PREPARE_RUNTIME_STATE_TASK_ID = "runner_vnext_prepare_runtime_state_v1"
RUNNER_VNEXT_EXTERNAL_ATTESTATION_TASK_ID = "runner_vnext_external_attestation"
RUNNER_VNEXT_EXACT_GREEN_CANARY_TASK_ID = "runner_vnext_exact_green_canary"
RUNNER_VNEXT_PREFLIGHT_SCHEMA = "skeleton.runner_vnext_readonly_preflight_receipt.v1"
RUNNER_VNEXT_PREPARE_RUNTIME_STATE_SCHEMA = (
    "skeleton.runner_vnext_prepare_runtime_state_receipt.v1"
)
RUNNER_VNEXT_EXTERNAL_ATTESTATION_PROFILE = "green_diagnostic_v1"
RUNNER_VNEXT_EXTERNAL_ATTESTATION_FILENAME = "runner-vnext-external-attestation.json"
RUNNER_VNEXT_FIXED_PRIVATE_STATE_ROOT = Path("/var/lib/skeleton/runner-vnext")
RUNNER_VNEXT_FIXED_PRIVATE_ENV_FILE = Path("/etc/skeleton-runner.env")
RUNNER_VNEXT_FIXED_PRIVATE_LEDGER_DB = (
    RUNNER_VNEXT_FIXED_PRIVATE_STATE_ROOT / "operation-ledger.sqlite3"
)
RUNNER_VNEXT_FIXED_PRIVATE_LEASE_DB = (
    RUNNER_VNEXT_FIXED_PRIVATE_STATE_ROOT / "lane-leases.sqlite3"
)
RUNNER_VNEXT_PRIVATE_STATE_ROOT = RUNNER_VNEXT_FIXED_PRIVATE_STATE_ROOT
RUNNER_VNEXT_PRIVATE_LEDGER_DB = RUNNER_VNEXT_FIXED_PRIVATE_LEDGER_DB
RUNNER_VNEXT_PRIVATE_LEASE_DB = RUNNER_VNEXT_FIXED_PRIVATE_LEASE_DB
RUNNER_VNEXT_PRIVATE_ENV_FILE = RUNNER_VNEXT_FIXED_PRIVATE_ENV_FILE
RUNNER_LEGACY_SERVICE_UNIT = "skeleton-runner-poll.service"
RUNNER_LEGACY_TIMER_UNIT = "skeleton-runner-poll.timer"
RUNNER_VNEXT_CODEGEN_CAPABILITIES = (
    "repository_read",
    "repository_write_allowlisted",
    "test_execution",
)
_RUNNER_VNEXT_NODE_SNAPSHOT_KEYS = frozenset(
    (
        "schema",
        "node_id",
        "generation",
        "route_rank",
        "capabilities",
        "supported_adapters",
        "supported_lanes",
        "privacy_classes",
        "resource_patterns",
        "observed_at",
        "expires_at",
        "attestation_ref",
    )
)


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
LABEL_PRIORITY_1 = "runner:priority-1"
LABEL_RUN_NOW = "queue:RUN_NOW"
LABEL_RUNNING = "runner:running"
LABEL_DONE = "runner:done"
LABEL_BLOCKED = "runner:blocked"
LABEL_NEEDS_OPERATOR = "runner:needs-operator"
LABEL_AGENT_TASK = "agent:task"
LABEL_WAITING_DEPENDENCY = "runner:waiting-dependency"
RECOVERABLE_BLOCKED_SELF_HEAL_OVERRIDE_HASH = one_time_override_hash(
    {"kind": "recoverable_blocked_dirty_continuation", "version": 2}
)
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
ACTIVE_EXECUTION_LABELS = frozenset((LABEL_READY, LABEL_RUN_NOW, LABEL_RUNNING))
TERMINAL_RUNNER_LABELS = frozenset((LABEL_DONE, LABEL_BLOCKED))
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
CODEGEN_BOOKKEEPING_ROOT_ENV = "SKELETON_CODEGEN_BOOKKEEPING_ROOT"
RUNNER_CODEX_PRIMARY_CODEGEN_TIMEOUT_SECONDS = 600
RUNNER_PRIVATE_MEMORY_ROOT_ENV = "SKELETON_RUNNER_PRIVATE_MEMORY_ROOT"
RUNNER_PRIVATE_MEMORY_DATASET_ENV = "SKELETON_RUNNER_PRIVATE_MEMORY_DATASET"
RUNNER_PRIVATE_MEMORY_REFS_ENV = "SKELETON_RUNNER_PRIVATE_MEMORY_REFS"
RUNNER_PRIVATE_MEMORY_REQUIRED_ENV = "SKELETON_RUNNER_PRIVATE_MEMORY_REQUIRED"
RUNNER_BRANCH_ENV = "SKELETON_RUNNER_BRANCH"
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
REPLENISH_RUNNER_QUEUE = "replenish_runner_queue"
CONTROL_PLANE_SELF_HEALING_RECOVERY = "control_plane_self_healing_recovery"
_QUEUE_RECOVERY_CANDIDATE_OVERRIDE: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "skeleton_queue_recovery_candidate_override",
    default=None,
)
_QUEUE_RECOVERY_SOURCE: ContextVar[str] = ContextVar(
    "skeleton_queue_recovery_source",
    default="compat",
)
_CURRENT_MAINTENANCE_TASK_ID: ContextVar[str | None] = ContextVar(
    "skeleton_current_maintenance_task_id",
    default=None,
)
_CURRENT_MAINTENANCE_ISSUE_NUMBER: ContextVar[int | None] = ContextVar(
    "skeleton_current_maintenance_issue_number",
    default=None,
)
INSPECT_ISSUE_WORKTREE_FOR_PUBLISH = "inspect_issue_worktree_for_publish"
PUBLISH_ISSUE_WORKTREE_PR = "publish_issue_worktree_pr"
PUBLISH_EXISTING_ISSUE_WORKTREE = "publish_existing_issue_worktree"
PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR = "publish_issue_worktree_to_existing_pr"
OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR = "overlay_registered_worktree_to_existing_pr"
PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR = "publish_target_project_issue_worktree_pr"
PUBLISH_CONTAINER_VALIDATION_WORKTREE = "publish_container_validation_worktree"
QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES = (
    "quarantine_stale_clean_skeleton_worktrees"
)
HOME_EDGE_01_READ_ONLY_DIAGNOSTIC = "home_edge_01_read_only_diagnostic"
HOME_EDGE_01_LAN_INVENTORY_READ_ONLY = "home_edge_01_lan_inventory_read_only"
HOME_EDGE_AUDIT_PERSIST_V1 = "home_edge_audit_persist_v1"
HOME_EDGE_01_DEBIAN_MEDIA_BOOTSTRAP_V1 = "home_edge_01_debian_media_bootstrap_v1"
HOME_EDGE_01_POST_MIGRATION_RECONCILE_V1 = "home_edge_01_post_migration_reconcile_v1"
HOME_EDGE_01_MEDIA_SOURCE_SNAPSHOT_V1 = "home_edge_01_media_source_snapshot_v1"
HOME_EDGE_01_ESP_LAB_STAGE1_ACTIVATION_V1 = (
    HOME_EDGE_ESP_LAB_STAGE1_ACTIVATION_TASK_ID
)
HOME_EDGE_01_ESP_LAB_STAGE1_SIGNER_INSTALL_V1 = (
    HOME_EDGE_ESP_LAB_STAGE1_SIGNER_INSTALL_TASK_ID
)
MAIL_GMAIL_PRIMARY_REGISTERED_ACTIVATION = "mail_gmail_primary_registered_activation_v1"
SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_V1 = (
    "skeleton_control_mcp_hetzner_activate_v1"
)
RUNNER_CONTROLLER_REFRESH_TRUST_ANCHOR_BUNDLE_V1 = (
    "runner_controller_refresh_trust_anchor_bundle_v1"
)
RUNNER_CODEX_PRIMARY_HEALTH_PROBE = "runner_codex_primary_health_probe"
RUNNER_CODEX_PRIMARY_HEALTH_TOKEN = "CODEX_HEALTH_OK"
RUNNER_CODEX_PRIMARY_HEALTH_TIMEOUT_SECONDS = 120
RUNNER_CODEX_PRIMARY_HEALTH_SCHEMA = (
    "skeleton.runner_codex_primary_health_probe_receipt.v1"
)
RUNTIME_MAINTENANCE_TASK_IDS = frozenset(
    (
        MAIL_GMAIL_READONLY_CANARY_TASK_ID,
        MAIL_GMAIL_PRIMARY_REGISTERED_ACTIVATION,
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
        ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY,
        HERMES_PRIVATE_MEMORY_BRIDGE_CHECK,
        PRIVATE_MEMORY_PHASE_A_INVENTORY,
        INSTALL_GRAPHIFY_RUNTIME,
        PREPARE_AUFMASS_PRIVATE_RUNTIME,
        RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        SUMMARIZE_AUFMASS_PRIVATE_REVIEW,
        BUILD_AUFMASS_PRIVATE_SHORTLIST,
        BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE,
        INSPECT_PR_MERGEABILITY,
        BACKFILL_SKELETON_MEMORY_RECENT,
        REPLENISH_RUNNER_QUEUE,
        CONTROL_PLANE_SELF_HEALING_RECOVERY,
        INSPECT_ISSUE_WORKTREE_FOR_PUBLISH,
        PUBLISH_ISSUE_WORKTREE_PR,
        PUBLISH_EXISTING_ISSUE_WORKTREE,
        PUBLISH_ISSUE_WORKTREE_TO_EXISTING_PR,
        OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR,
        PUBLISH_TARGET_PROJECT_ISSUE_WORKTREE_PR,
        PUBLISH_CONTAINER_VALIDATION_WORKTREE,
        QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES,
        HOME_EDGE_01_READ_ONLY_DIAGNOSTIC,
        HOME_EDGE_01_LAN_INVENTORY_READ_ONLY,
        HOME_EDGE_AUDIT_PERSIST_V1,
        HOME_EDGE_01_DEBIAN_MEDIA_BOOTSTRAP_V1,
        HOME_EDGE_01_POST_MIGRATION_RECONCILE_V1,
        HOME_EDGE_01_MEDIA_SOURCE_SNAPSHOT_V1,
        HOME_EDGE_01_ESP_LAB_STAGE1_ACTIVATION_V1,
        HOME_EDGE_01_ESP_LAB_STAGE1_SIGNER_INSTALL_V1,
        "runner_controller_repair_codex_state_mount_v1",
        SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_V1,
        RUNNER_CONTROLLER_REFRESH_TRUST_ANCHOR_BUNDLE_V1,
        RUNNER_CODEX_PRIMARY_HEALTH_PROBE,
        RUNNER_VNEXT_READONLY_PREFLIGHT_TASK_ID,
        RUNNER_VNEXT_PREPARE_RUNTIME_STATE_TASK_ID,
        RUNNER_VNEXT_EXTERNAL_ATTESTATION_TASK_ID,
        RUNNER_VNEXT_EXACT_GREEN_CANARY_TASK_ID,
        BUILD_AND_LOCAL_OTA_OPERATION,
        PREPARE_PRIVATE_STATIC_SITE_HANDOFF,
        DEPLOY_PRIVATE_STATIC_SITE,
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
ROUTE_APPROVAL_KIND_GENERIC = "generic"
ROUTE_APPROVAL_KIND_PROTECTED = "protected"
ROUTE_APPROVAL_KIND_RECOVERY = "recovery"
TRUSTED_GENERIC_APPROVAL_REFERENCES = frozenset(
    (
        "GENERIC_RUNNER_REPAIR_TEST_MERGE_RUNTIME_SYNC_20260715",
    )
)
TRUSTED_PROTECTED_APPROVAL_REFERENCES = frozenset(
    (
        "EXPLICIT_PROTECTED_RUNNER_REPAIR_TEST_MERGE_RUNTIME_SYNC_20260715",
    )
)
TRUSTED_RECOVERY_APPROVAL_REFERENCES = frozenset(
    (
        "EXPLICIT_RECOVERY_RUNNER_REPAIR_TEST_MERGE_RUNTIME_SYNC_20260715",
    )
)
TRUSTED_APPROVAL_COMMENT_IDS_BY_REFERENCE = {
    "GENERIC_RUNNER_REPAIR_TEST_MERGE_RUNTIME_SYNC_20260715": "5002100944",
    "EXPLICIT_PROTECTED_RUNNER_REPAIR_TEST_MERGE_RUNTIME_SYNC_20260715": "5002100945",
    "EXPLICIT_RECOVERY_RUNNER_REPAIR_TEST_MERGE_RUNTIME_SYNC_20260715": "5002100946",
}
RECOVERY_ONLY_MAINTENANCE_TASK_IDS = frozenset(
    (
        OVERLAY_REGISTERED_WORKTREE_TO_EXISTING_PR,
    )
)
PROTECTED_MAINTENANCE_TASK_IDS = frozenset(
    (
        SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
        ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG,
        RUNTIME_SYNC_MAIN,
        RECOVER_SKELETON_CHECKOUT,
        ENSURE_PROJECT_CHECKOUT,
        PREFLIGHT_PR_REFRESH,
        LOOP_ENGINE_PACKET,
        BACKFILL_SKELETON_MEMORY_RECENT,
        INSTALL_GRAPHIFY_RUNTIME,
        PREPARE_AUFMASS_PRIVATE_RUNTIME,
        RUN_AUFMASS_PRIVATE_DXF_REVIEW,
        SUMMARIZE_AUFMASS_PRIVATE_REVIEW,
        BUILD_AUFMASS_PRIVATE_SHORTLIST,
        BUILD_AUFMASS_PRIVATE_AREA_SCHEDULE,
        QUARANTINE_STALE_CLEAN_SKELETON_WORKTREES,
        REPLENISH_RUNNER_QUEUE,
        RUNNER_CODEX_PRIMARY_HEALTH_PROBE,
        RUNNER_VNEXT_PREPARE_RUNTIME_STATE_TASK_ID,
        RUNNER_VNEXT_EXTERNAL_ATTESTATION_TASK_ID,
        RUNNER_VNEXT_EXACT_GREEN_CANARY_TASK_ID,
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
VALIDATE_PR_BRANCH_ROUTE_SAFE_EXCEPTIONS = (
    OSError,
    subprocess.SubprocessError,
)
VALIDATION_FAILED_OUTPUT_LIMIT = 4000
VALIDATION_FAILED_OUTPUT_TRUNCATED_MARKER = (
    "[Runner validation output truncated to 4000 characters.]"
)
QUARANTINE_REMOVE_FAILED_OUTPUT_LIMIT = 1200
QUARANTINE_REMOVE_FAILED_OUTPUT_TRUNCATED_MARKER = (
    "[Runner quarantine remove output truncated to 1200 characters.]"
)
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
_SKELETON_RECOVERABLE_BRANCH_RE = re.compile(r"^runner/issue-[1-9][0-9]*$")
_SAFE_TARGET_BASE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
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
        "attempt",
        "approved_override_hash",
        "approval_status",
        "approved_head_sha",
        "artifact_count",
        "artifact_id",
        "actions_executed",
        "auth_binding_present",
        "auth_mode",
        "audit_id",
        "audit_persist_status",
        "audit_receipt_hash",
        "autologin_status",
        "aggregate_source_repaired",
        "aggregate_status",
        "base_branch",
        "base_ref_oid",
        "base_ref",
        "base_sha",
        "behind_by",
        "blocked_write_status",
        "branch",
        "build_ms",
        "canon_note",
        "canonical_memory_post_step",
        "canaries_executed",
        "candidate_count",
        "ready_depth_before",
        "selected_count",
        "selected_model",
        "selected_issues",
        "waiting_dependency_count",
        "telegram_notifications",
        "chat_export_candidate_count",
        "changed_file",
        "changed_file_count",
        "changed_files",
        "final_postcheck_receipt_hash",
        "failure_class",
        "failure_key",
        "fallback_invoked",
        "private_artifact_hash_matches",
        "private_artifact_written",
        "changed_files_count",
        "changed_tracked_files",
        "changed_tracked_files_count",
        "check",
        "checkout_clean",
        "checkout_head_sha",
        "checkout_path",
        "checkout_sync_state",
        "context_hash",
        "certificate_sha256_fingerprint",
        "command_unavailable_reason",
        "content_files_read",
        "compare_ahead_by",
        "compare_behind_by",
        "compare_state",
        "compare_status",
        "canonical_write_enabled",
        "constructed_head_sha",
        "conflict_status",
        "current_branch",
        "current_brother_guard_timer_status",
        "current_brother_service_status",
        "decision_records_skipped",
        "decision_records_written",
        "decision",
        "delegated_merge_verdict",
        "degraded",
        "diagnostic_count",
        "device_count",
        "document_candidate_count",
        "display_manager_status",
        "responsive_count",
        "gateway_presence",
        "gateway_postcheck_status",
        "gateway_status",
        "gallery_status",
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
        "env_file_bound",
        "event",
        "excluded_infrastructure_cache_name_count",
        "excluded_secret_like_count",
        "external_side_effects_executed",
        "activation_executed",
        "action_present",
        "authoritative_stores_initialized",
        "dependency_installed_by_operation",
        "device_canary",
        "exact_base_changed_files_count",
        "evidence_ref",
        "executable_available",
        "existing_pr_lookup",
        "existing_pr_head_sha",
        "existing_pr_url",
        "expected_current_sha_equal",
        "expected_existing_pr_head_sha",
        "expected_branch",
        "expected_main_sha",
        "expected_head_sha",
        "expected_pr_head_branch",
        "expected_pr_head_sha",
        "expected_source_branch",
        "expected_target_branch",
        "expected_target_head_sha",
        "expires_at",
        "exit_code",
        "explicit_recovery_route",
        "executor_receipt_hash",
        "exact_current_main",
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
        "health_route_present",
        "hermes_bridge_status",
        "hermes_gateway_contract",
        "hermes_memory_operation_count",
        "hermes_memory_smoke_status",
        "host_id_sha256_12",
        "handoff_ref",
        "idempotent_reuse",
        "input_row_count",
        "input_table_count",
        "installed_skill_platform_count",
        "installed_artifacts_verified",
        "installer_sha256",
        "inventory_schema",
        "issue_number",
        "issue_worktree",
        "issue_worktree_id",
        "kernel_release",
        "ledger_events_written",
        "legacy_runner_touched",
        "legacy_service_load_state",
        "legacy_timer_load_state",
        "listed_worktrees_count",
        "machine",
        "maintenance_task_id",
        "mutation_executor_receipt_hash",
        "managed_version_marker_count",
        "manifest_candidate_count",
        "model_credentials_used",
        "merge_action",
        "mergeable",
        "mergeable_state",
        "memory_events_existing",
        "memory_events_written",
        "memory_database_candidate_count",
        "missing_file",
        "missing_dependency_module",
        "mode",
        "model_credentials_removed_from_smoke",
        "mutation_mode",
        "mutation_performed",
        "mutation_started",
        "durable_handoff_status",
        "network_disabled",
        "network_provider_enabled",
        "needs_operator_notification",
        "next_retry_at",
        "next_action",
        "next_operator_action",
        "node_identity_status",
        "operation_id",
        "open_issues_count",
        "open_pull_requests_count",
        "os_identity_status",
        "orient_status",
        "other_candidate_count",
        "boot_id_unchanged",
        "brother_status",
        "cast_status",
        "chromium_status",
        "pilot_mode",
        "pilot_summary_schema",
        "mpv_status",
        "ports_disabled",
        "ports_enabled",
        "post_push_pr_metadata_source",
        "pr_number",
        "pr_metadata_source",
        "pr_state",
        "pr_title",
        "pr_url",
        "payload_hash",
        "publish_override_hash",
        "private_memory_db_configured",