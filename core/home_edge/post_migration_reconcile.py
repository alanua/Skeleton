from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4

from core.home_edge.executor import HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV, execute_home_edge_request


TASK_ID = "home_edge_01_post_migration_reconcile_v1"
REPOSITORY = "alanua/Skeleton"
TARGET_NODE = "home-edge-01"
OPERATOR_APPROVAL = "EXPLICIT_RECONCILE_HOME_EDGE_POST_MIGRATION_20260808"
APPROVAL_REF = OPERATOR_APPROVAL
IDEMPOTENCY_KEY = "home-edge-01-post-migration-reconcile-20260808-v3"
REQUEST_TIMEOUT_SECONDS = 900
RECEIPT_SCHEMA = "skeleton.home_edge.post_migration_reconcile_receipt.v1"
CANONICAL_MEMORY_POST_STEP = "home_edge_audit_persist_v1"
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

DESKTOP_USER = "oleksii"
DESKTOP_UID = 1000
DESKTOP_HOME = "/home/oleksii"
USER_RUNTIME = "/run/user/1000"
USER_DBUS = "unix:path=/run/user/1000/bus"
STALE_HOME_PREFIX = "/home/valertos08"
NEW_HOME_PREFIX = DESKTOP_HOME

GALLERY_REFRESH = "/home/oleksii/.local/bin/home-edge-screensaver-gallery-refresh"
GALLERY_VERIFY = "/home/oleksii/.local/bin/home-edge-screensaver-verify-v9"
BROTHER_VERIFY = "/home/oleksii/.local/bin/brother-scan-key-verify"
AGGREGATE_VERIFY = "/home/oleksii/.local/bin/home-edge-platform-repair-verify"
REGISTRY_CLI = "/home/oleksii/.local/bin/skeleton-devices"
CAST_CONTROL = "/home/oleksii/.local/bin/skeleton-cast-control"
POINTER_STATUS = "/home/oleksii/.local/bin/home-edge-pointer-status"
WATCHDOG_STATUS = "/home/oleksii/.local/bin/home-edge-media-watchdog"
CURRENT_BROTHER_SERVICE = "brother-scan-key.service"
CURRENT_BROTHER_GUARD_TIMER = "brother-scan-key-guard.timer"
STALE_AGGREGATE_UNIT_LITERAL = "brother_guard_v2.service"

RECEIPT_FIELDS = (
    "maintenance_task_id",
    "os_identity_status",
    "node_identity_status",
    "boot_id_unchanged",
    "registry_status",
    "gallery_status",
    "brother_status",
    "aggregate_status",
    "cast_status",
    "pointer_status",
    "watchdog_status",
    "watchdog_critical_count",
    "watchdog_warning_count",
    "refresh_count",
    "aggregate_source_repaired",
    "stale_before_count",
    "stale_after_count",
    "changed_file_count",
    "system_failed_unit_count",
    "user_failed_unit_count",
    "current_brother_service_status",
    "current_brother_guard_timer_status",
    "rollback_ready",
    "rollback_applied",
    "mutation_executor_receipt_hash",
    "audit_receipt_hash",
    "stable_reason",
    "success_criteria",
    "canonical_memory_post_step",
)

_ALLOWED_FIELDS = frozenset(
    {
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Expected Main SHA",
        "Operator Approval",
        "Target",
    }
)
_FIELD_RE = re.compile(r"^\s*(?P<field>[A-Za-z][A-Za-z0-9 _-]{0,80}):\s*(?P<value>.*?)\s*$")
_PUBLIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")


@dataclass(frozen=True)
class RuntimeInput:
    repository: str
    expected_main_sha: str
    operator_approval: str
    target: str


def execute_post_migration_reconcile_task(
    body: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    runtime_input = parse_runtime_input(body)
    validate_main_sha(
        runtime_input.expected_main_sha,
        registered_clean_main_sha=registered_clean_main_sha,
        github_main_sha=github_main_sha,
    )
    request = build_reconcile_request(environment=environment)
    executor_receipt = execute_home_edge_request(request.to_mapping())
    public = public_receipt_from_executor_stdout(executor_receipt.to_mapping())
    public["mutation_executor_receipt_hash"] = executor_receipt.receipt_hash
    public["audit_receipt_hash"] = _audit_hash(public)
    if not success_criteria_met(public):
        public["success_criteria"] = "not_met"
        public["audit_receipt_hash"] = _audit_hash(public)
    return public


def parse_runtime_input(body: str) -> RuntimeInput:
    fields: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in _metadata_lines(body):
        match = _FIELD_RE.match(line)
        if match is None:
            continue
        field = match.group("field").strip()
        value = match.group("value").strip()
        if not value:
            continue
        if field in fields:
            duplicates.add(field)
        fields[field] = value
    if duplicates:
        raise ValueError("duplicate_runtime_input_field")
    unknown = sorted(set(fields) - _ALLOWED_FIELDS)
    if unknown:
        raise ValueError("unknown_runtime_input_field")
    if fields.get("Mode") != "RUNTIME_MAINTENANCE_TASK":
        raise ValueError("runtime_mode_mismatch")
    if fields.get("Maintenance Task ID") != TASK_ID:
        raise ValueError("maintenance_task_id_mismatch")
    runtime_input = RuntimeInput(
        repository=fields.get("Repository", ""),
        expected_main_sha=fields.get("Expected Main SHA", ""),
        operator_approval=fields.get("Operator Approval", ""),
        target=fields.get("Target", ""),
    )
    if runtime_input.repository != REPOSITORY:
        raise ValueError("repository_mismatch")
    if EXPECTED_MAIN_SHA_RE.fullmatch(runtime_input.expected_main_sha) is None:
        raise ValueError("expected_main_sha_malformed")
    if runtime_input.operator_approval != OPERATOR_APPROVAL:
        raise ValueError("operator_approval_mismatch")
    if runtime_input.target != TARGET_NODE:
        raise ValueError("target_mismatch")
    return runtime_input


def validate_main_sha(
    expected_main_sha: str,
    *,
    registered_clean_main_sha: str,
    github_main_sha: str,
) -> None:
    if EXPECTED_MAIN_SHA_RE.fullmatch(registered_clean_main_sha or "") is None:
        raise ValueError("registered_clean_main_sha_unavailable")
    if EXPECTED_MAIN_SHA_RE.fullmatch(github_main_sha or "") is None:
        raise ValueError("github_main_sha_unavailable")
    if expected_main_sha != registered_clean_main_sha:
        raise ValueError("registered_clean_main_sha_mismatch")
    if expected_main_sha != github_main_sha:
        raise ValueError("github_main_sha_mismatch")


def build_reconcile_request(
    *, environment: Mapping[str, str] | None = None
) -> HomeEdgeExecRequest:
    env = os.environ if environment is None else environment
    secret = env.get(EXEC_HMAC_SECRET_ENV, "")
    if not secret:
        raise ValueError("home_edge_exec_hmac_secret_missing")
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": "privileged_mutation",
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "operator_approval_ref": APPROVAL_REF,
            "idempotency_key": IDEMPOTENCY_KEY,
            "run_as": "root",
            "mode": "script",
            "script": RECONCILE_SCRIPT,
            "script_interpreter": "bash",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"{TASK_ID}-{uuid4()}",
            "max_output_bytes": 65536,
        }
    )
    signature = sign_request(request, secret)
    return HomeEdgeExecRequest.from_mapping({**request.to_mapping(include_signature=False), "signature": signature})


def public_receipt_from_executor_stdout(receipt: Mapping[str, Any]) -> dict[str, object]:
    if receipt.get("status") != "ok" or receipt.get("exit_code") != 0:
        return _blocked_receipt("executor_receipt_not_ok")
    stdout = receipt.get("stdout")
    if not isinstance(stdout, str):
        return _blocked_receipt("executor_stdout_missing")
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError:
        return _blocked_receipt("executor_stdout_not_json")
    if not isinstance(decoded, dict):
        return _blocked_receipt("executor_stdout_not_object")
    return sanitize_public_receipt(decoded)


def sanitize_public_receipt(receipt: Mapping[str, Any]) -> dict[str, object]:
    sanitized: dict[str, object] = {}
    for field in RECEIPT_FIELDS:
        if field not in receipt:
            raise ValueError("receipt_field_missing")
        value = receipt[field]
        if isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            if value < 0:
                raise ValueError("receipt_field_not_public_safe")
            sanitized[field] = value
        elif isinstance(value, str) and _PUBLIC_VALUE_RE.fullmatch(value):
            sanitized[field] = value
        else:
            raise ValueError("receipt_field_not_public_safe")
    if sanitized["maintenance_task_id"] != TASK_ID:
        raise ValueError("receipt_task_id_mismatch")
    if sanitized["canonical_memory_post_step"] != CANONICAL_MEMORY_POST_STEP:
        raise ValueError("canonical_memory_post_step_mismatch")
    return sanitized


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [f"{field}={receipt[field]}" for field in RECEIPT_FIELDS]


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("os_identity_status") == "verified"
        and receipt.get("node_identity_status") == "verified"
        and receipt.get("boot_id_unchanged") is True
        and receipt.get("registry_status") == "healthy"
        and receipt.get("gallery_status") in {"already_healthy", "repaired_healthy"}
        and receipt.get("brother_status") == "healthy"
        and receipt.get("aggregate_status") == "healthy"
        and receipt.get("cast_status") == "healthy"
        and receipt.get("pointer_status") == "healthy"
        and receipt.get("watchdog_status") == "healthy"
        and receipt.get("watchdog_critical_count") == 0
        and receipt.get("watchdog_warning_count") == 0
        and receipt.get("stale_after_count") == 0
        and receipt.get("system_failed_unit_count") == 0
        and receipt.get("user_failed_unit_count") == 0
        and receipt.get("current_brother_service_status") == "active"
        and receipt.get("current_brother_guard_timer_status") == "active"
        and receipt.get("rollback_ready") is True
        and receipt.get("rollback_applied") is False
        and receipt.get("canonical_memory_post_step") == CANONICAL_MEMORY_POST_STEP
    )


def brother_json_is_healthy(payload: Mapping[str, Any]) -> bool:
    if payload.get("status") != "healthy":
        return False
    checks = payload.get("checks")
    if checks is None:
        return True
    if not isinstance(checks, Mapping):
        return False
    return all(isinstance(value, bool) and value for value in checks.values())


def cast_json_is_healthy(payload: Mapping[str, Any]) -> bool:
    return payload.get("status") == "ok" and payload.get("service") == "skeleton-cast"


def pointer_json_is_healthy(payload: Mapping[str, Any]) -> bool:
    if payload.get("api_backend") not in (None, "uinput_pointer_broker"):
        return False
    return (
        payload.get("service_active") is True
        and payload.get("socket_exists") is True
        and payload.get("uinput_device_registered") is True
        and payload.get("broker_response") == "ok"
    )


def watchdog_json_status(payload: Mapping[str, Any]) -> tuple[bool, int, int]:
    last = payload.get("last")
    if not isinstance(last, Mapping):
        return False, 0, 0
    summary = last.get("summary")
    if not isinstance(summary, Mapping):
        return False, 0, 0
    critical = summary.get("critical")
    warnings = summary.get("warnings")
    if not isinstance(critical, int) or isinstance(critical, bool):
        return False, 0, 0
    if not isinstance(warnings, int) or isinstance(warnings, bool):
        return False, 0, 0
    return (
        last.get("overall") == "healthy"
        and last.get("healthy") is True
        and critical == 0
        and warnings == 0,
        critical,
        warnings,
    )


def _metadata_lines(body: str) -> list[str]:
    metadata = (body or "").split("```task", 1)[0]
    return [line for line in metadata.splitlines() if not line.lstrip().startswith("#")]


def _blocked_receipt(reason: str) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": TASK_ID,
        "os_identity_status": "unverified",
        "node_identity_status": "unverified",
        "boot_id_unchanged": False,
        "registry_status": "blocked",
        "gallery_status": "blocked",
        "brother_status": "blocked",
        "aggregate_status": "blocked",
        "cast_status": "blocked",
        "pointer_status": "blocked",
        "watchdog_status": "blocked",
        "watchdog_critical_count": 0,
        "watchdog_warning_count": 0,
        "refresh_count": 0,
        "aggregate_source_repaired": False,
        "stale_before_count": 0,
        "stale_after_count": 0,
        "changed_file_count": 0,
        "system_failed_unit_count": 0,
        "user_failed_unit_count": 0,
        "current_brother_service_status": "blocked",
        "current_brother_guard_timer_status": "blocked",
        "rollback_ready": True,
        "rollback_applied": False,
        "mutation_executor_receipt_hash": "unavailable",
        "audit_receipt_hash": "pending",
        "stable_reason": reason,
        "success_criteria": "not_met",
        "canonical_memory_post_step": CANONICAL_MEMORY_POST_STEP,
    }
    receipt["audit_receipt_hash"] = _audit_hash(receipt)
    return receipt


def _audit_hash(receipt: Mapping[str, object]) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key in RECEIPT_FIELDS and key != "audit_receipt_hash"
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


RECONCILE_SCRIPT = r'''set -uo pipefail
TASK_ID="home_edge_01_post_migration_reconcile_v1"
STATE_ROOT="/var/lib/skeleton/home-edge-01/post-migration-reconcile-v1"
ROLLBACK_ROOT="$STATE_ROOT/rollback"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
ROLLBACK_DIR="$ROLLBACK_ROOT/$RUN_ID"
MANIFEST="$ROLLBACK_DIR/manifest.tsv"
LOG_DIR="$ROLLBACK_DIR/logs"
DESKTOP_USER="oleksii"
DESKTOP_UID="1000"
DESKTOP_HOME="/home/oleksii"
USER_RUNTIME="/run/user/1000"
USER_DBUS="unix:path=/run/user/1000/bus"
OLD_HOME="/home/valertos08"
NEW_HOME="/home/oleksii"
GALLERY_REFRESH="/home/oleksii/.local/bin/home-edge-screensaver-gallery-refresh"
GALLERY_VERIFY="/home/oleksii/.local/bin/home-edge-screensaver-verify-v9"
BROTHER_VERIFY="/home/oleksii/.local/bin/brother-scan-key-verify"
AGGREGATE_VERIFY="/home/oleksii/.local/bin/home-edge-platform-repair-verify"
REGISTRY_CLI="/home/oleksii/.local/bin/skeleton-devices"
CAST_CONTROL="/home/oleksii/.local/bin/skeleton-cast-control"
POINTER_STATUS="/home/oleksii/.local/bin/home-edge-pointer-status"
WATCHDOG_STATUS="/home/oleksii/.local/bin/home-edge-media-watchdog"
CURRENT_BROTHER_SERVICE="brother-scan-key.service"
CURRENT_BROTHER_GUARD_TIMER="brother-scan-key-guard.timer"
STALE_AGGREGATE_UNIT_LITERAL="brother_guard_v2.service"
CURRENT_AGGREGATE_UNIT_LITERAL="brother-scan-key.service"
CANONICAL_MEMORY_POST_STEP="home_edge_audit_persist_v1"
rollback_ready=false
rollback_applied=false
receipt_emitted=false
refresh_count=0
aggregate_source_repaired=false
stale_before_count=0
stale_after_count=0
changed_file_count=0
system_failed_unit_count=0
user_failed_unit_count=0
watchdog_critical_count=0
watchdog_warning_count=0
registry_status="blocked"
gallery_status="blocked"
brother_status="blocked"
aggregate_status="blocked"
cast_status="blocked"
pointer_status="blocked"
watchdog_status="blocked"
current_brother_service_status="blocked"
current_brother_guard_timer_status="blocked"
stable_reason="completed"
system_units_changed=false
user_units_changed=false
boot_id_before="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
json_field() {
  python3 - "$1" "$2" <<'PY'
import json, sys
path, field = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path, "r", encoding="utf-8"))
except Exception:
    sys.exit(1)
value = data
for part in field.split("."):
    if not isinstance(value, dict) or part not in value:
        sys.exit(1)
    value = value[part]
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, int) and not isinstance(value, bool):
    print(value)
elif isinstance(value, str):
    print(value)
else:
    sys.exit(1)
PY
}
emit_receipt() {
  if [ "$receipt_emitted" = true ]; then exit 70; fi
  receipt_emitted=true
  local criteria="$1" hash
  hash="$(printf '%s:%s:%s:%s:%s:%s:%s:%s' "$TASK_ID" "$registry_status" "$gallery_status" "$brother_status" "$aggregate_status" "$stale_after_count" "$stable_reason" "$criteria" | sha256sum | awk '{print $1}')"
  printf '{"maintenance_task_id":"%s",' "$TASK_ID"
  printf '"os_identity_status":"verified",'
  printf '"node_identity_status":"verified",'
  printf '"boot_id_unchanged":%s,' "$([ "$boot_id_before" = "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)" ] && echo true || echo false)"
  printf '"registry_status":"%s",' "$registry_status"
  printf '"gallery_status":"%s",' "$gallery_status"
  printf '"brother_status":"%s",' "$brother_status"
  printf '"aggregate_status":"%s",' "$aggregate_status"
  printf '"cast_status":"%s",' "$cast_status"
  printf '"pointer_status":"%s",' "$pointer_status"
  printf '"watchdog_status":"%s",' "$watchdog_status"
  printf '"watchdog_critical_count":%s,' "$watchdog_critical_count"
  printf '"watchdog_warning_count":%s,' "$watchdog_warning_count"
  printf '"refresh_count":%s,' "$refresh_count"
  printf '"aggregate_source_repaired":%s,' "$aggregate_source_repaired"
  printf '"stale_before_count":%s,' "$stale_before_count"
  printf '"stale_after_count":%s,' "$stale_after_count"
  printf '"changed_file_count":%s,' "$changed_file_count"
  printf '"system_failed_unit_count":%s,' "$system_failed_unit_count"
  printf '"user_failed_unit_count":%s,' "$user_failed_unit_count"
  printf '"current_brother_service_status":"%s",' "$current_brother_service_status"
  printf '"current_brother_guard_timer_status":"%s",' "$current_brother_guard_timer_status"
  printf '"rollback_ready":%s,' "$rollback_ready"
  printf '"rollback_applied":%s,' "$rollback_applied"
  printf '"mutation_executor_receipt_hash":"pending",'
  printf '"audit_receipt_hash":"%s",' "$hash"
  printf '"stable_reason":"%s",' "$stable_reason"
  printf '"success_criteria":"%s",' "$criteria"
  printf '"canonical_memory_post_step":"%s"}\n' "$CANONICAL_MEMORY_POST_STEP"
}
block() { stable_reason="$1"; emit_receipt not_met; exit 10; }
bounded_log() { printf '%s/%s.log' "$LOG_DIR" "$1"; }
run_user() {
  runuser -u oleksii -- env HOME=/home/oleksii XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "$@"
}
ensure_private_state() {
  install -d -m 0700 "$STATE_ROOT" "$ROLLBACK_ROOT" "$ROLLBACK_DIR" "$LOG_DIR" || return 1
  : >"$MANIFEST" || return 1
  chmod 0600 "$MANIFEST" || return 1
  rollback_ready=true
  [ -r "$MANIFEST" ] || return 1
}
validate_exec() {
  local path="$1" uid mode
  [ -f "$path" ] || return 1
  [ ! -L "$path" ] || return 1
  [ -x "$path" ] || return 1
  uid="$(stat -c '%u' "$path" 2>/dev/null)" || return 1
  [ "$uid" = "0" ] || [ "$uid" = "1000" ] || return 1
  mode="$(stat -c '%a' "$path" 2>/dev/null)" || return 1
  [ $((8#$mode & 002)) -eq 0 ] || return 1
}
file_editable() {
  local path="$1" uid mode
  [ -f "$path" ] || return 1
  [ ! -L "$path" ] || return 1
  uid="$(stat -c '%u' "$path" 2>/dev/null)" || return 1
  [ "$uid" = "0" ] || [ "$uid" = "1000" ] || return 1
  mode="$(stat -c '%a' "$path" 2>/dev/null)" || return 1
  [ $((8#$mode & 002)) -eq 0 ] || return 1
}
backup_file() {
  local path="$1" rel backup mode uid gid sha kind="$2"
  rel="${path#/}"
  backup="$ROLLBACK_DIR/files/$rel"
  install -d -m 0700 "$(dirname "$backup")" || return 1
  cp -a "$path" "$backup" || return 1
  chmod 0600 "$backup" || return 1
  mode="$(stat -c '%a' "$path")" || return 1
  uid="$(stat -c '%u' "$path")" || return 1
  gid="$(stat -c '%g' "$path")" || return 1
  sha="$(sha256sum "$path" | awk '{print $1}')" || return 1
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$path" "$kind" "$mode" "$uid" "$gid" "$sha" "$backup" >>"$MANIFEST" || return 1
}
atomic_replace() {
  local path="$1" src="$2" mode uid gid tmp
  mode="$(stat -c '%a' "$path")" || return 1
  uid="$(stat -c '%u' "$path")" || return 1
  gid="$(stat -c '%g' "$path")" || return 1
  tmp="$(mktemp "$(dirname "$path")/.reconcile.XXXXXX")" || return 1
  cp "$src" "$tmp" || { rm -f "$tmp"; return 1; }
  chown "$uid:$gid" "$tmp" || { rm -f "$tmp"; return 1; }
  chmod "$mode" "$tmp" || { rm -f "$tmp"; return 1; }
  mv -f "$tmp" "$path" || { rm -f "$tmp"; return 1; }
}
manager_reload_for() {
  case "$1" in
    system) systemctl daemon-reload >"$(bounded_log system_daemon_reload)" 2>&1 ;;
    user) run_user systemctl --user daemon-reload >"$(bounded_log user_daemon_reload)" 2>&1 ;;
  esac
}
restore_all() {
  local path kind mode uid gid sha backup restored_sha failed=false
  while IFS="$(printf '\t')" read -r path kind mode uid gid sha backup; do
    [ -n "$path" ] || continue
    cp -a "$backup" "$path" || { failed=true; continue; }
    chown "$uid:$gid" "$path" 2>/dev/null || true
    chmod "$mode" "$path" 2>/dev/null || true
    restored_sha="$(sha256sum "$path" | awk '{print $1}')" || { failed=true; continue; }
    [ "$restored_sha" = "$sha" ] || failed=true
    [ "$kind" = "system_unit" ] && system_units_changed=true
    [ "$kind" = "user_unit" ] && user_units_changed=true
  done <"$MANIFEST"
  [ "$system_units_changed" = true ] && manager_reload_for system
  [ "$user_units_changed" = true ] && manager_reload_for user
  [ "$failed" = false ] || return 1
  rollback_applied=true
}
fail_after_mutation() {
  local reason="$1"
  restore_all || { stable_reason="rollback_failed"; emit_receipt not_met; exit 60; }
  stable_reason="$reason"
  emit_receipt not_met
  exit 50
}
healthy_brother_json() {
  python3 - "$1" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
except Exception:
    sys.exit(1)
if data.get("status") != "healthy":
    sys.exit(1)
checks = data.get("checks")
if checks is not None:
    if not isinstance(checks, dict) or not all(isinstance(v, bool) and v for v in checks.values()):
        sys.exit(1)
PY
}
healthy_cast_json() {
  [ "$(json_field "$1" status 2>/dev/null || true)" = "ok" ] && [ "$(json_field "$1" service 2>/dev/null || true)" = "skeleton-cast" ]
}
healthy_pointer_json() {
  [ "$(json_field "$1" service_active 2>/dev/null || true)" = "true" ] &&
  [ "$(json_field "$1" socket_exists 2>/dev/null || true)" = "true" ] &&
  [ "$(json_field "$1" uinput_device_registered 2>/dev/null || true)" = "true" ] &&
  [ "$(json_field "$1" broker_response 2>/dev/null || true)" = "ok" ] &&
  { backend="$(json_field "$1" api_backend 2>/dev/null || true)"; [ -z "$backend" ] || [ "$backend" = "uinput_pointer_broker" ]; }
}
watchdog_counts() {
  watchdog_critical_count="$(json_field "$1" last.summary.critical 2>/dev/null || echo 0)"
  watchdog_warning_count="$(json_field "$1" last.summary.warnings 2>/dev/null || echo 0)"
  [ "$(json_field "$1" last.overall 2>/dev/null || true)" = "healthy" ] &&
  [ "$(json_field "$1" last.healthy 2>/dev/null || true)" = "true" ] &&
  [ "$watchdog_critical_count" = "0" ] &&
  [ "$watchdog_warning_count" = "0" ]
}
extract_gallery_count() {
  python3 - "$1" <<'PY'
import json, sys
allow = ("count", "item_count", "gallery_count", "image_count", "accepted_count")
try:
    data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
except Exception:
    print(0); sys.exit(0)
for key in allow:
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        print(value); sys.exit(0)
print(0)
PY
}
enumerate_scope() {
  find /home/oleksii/.local/bin -maxdepth 1 -type f ! -xtype l \( -name 'home-edge-*' -o -name 'skeleton-*' -o -name 'tv-mode*' \) ! -name '*\.bak*' ! -name '*\.backup*' ! -name '*\.before-*' ! -name '*\.pre-*' ! -name '*\.old*' ! -name '*\.archive*' ! -name '*__pycache__*' -print0 2>/dev/null
  find /home/oleksii/.config/systemd/user -maxdepth 1 -type f ! -xtype l \( -name '*.service' -o -name '*.timer' \) -print0 2>/dev/null
  find /etc/systemd/system -maxdepth 1 -type f ! -xtype l \( -name 'home-edge-*.service' -o -name 'home-edge-*.timer' -o -name 'skeleton-*.service' -o -name 'skeleton-*.timer' -o -name 'brother-*.service' -o -name 'brother-*.timer' \) -print0 2>/dev/null
}
replace_stale_paths() {
  local path="$1" kind="$2" tmp code
  file_editable "$path" || return 2
  tmp="$(mktemp)" || return 2
  python3 - "$path" "$OLD_HOME" "$NEW_HOME" >"$tmp" <<'PY'
import os, re, sys
path, old, new = sys.argv[1:4]
data = open(path, "rb").read()
text = data.decode("utf-8")
path_re = re.compile(re.escape(old) + r"[A-Za-z0-9_./:@%+=,-]*")
out = []
pos = 0
changed = False
for match in path_re.finditer(text):
    out.append(text[pos:match.start()])
    old_path = match.group(0)
    mapped = new + old_path[len(old):]
    if not os.path.exists(mapped):
        sys.exit(3)
    out.append(mapped)
    changed = True
    pos = match.end()
out.append(text[pos:])
result = "".join(out)
if old in result:
    sys.exit(4)
sys.stdout.write(result)
sys.exit(1 if changed else 0)
PY
  code=$?
  if [ "$code" = "1" ]; then
    backup_file "$path" "$kind" || { rm -f "$tmp"; return 2; }
    atomic_replace "$path" "$tmp" || { rm -f "$tmp"; return 2; }
    changed_file_count=$((changed_file_count + 1))
    rm -f "$tmp"
    return 1
  fi
  rm -f "$tmp"
  [ "$code" = "0" ] && return 0
  return "$code"
}
[ "$(id -u)" = "0" ] || block missing_root_execution
[ -r /etc/os-release ] || block os_release_missing
. /etc/os-release
[ "${ID:-}" = "debian" ] || block os_not_debian
case "${VERSION_ID:-}" in 13*) ;; *) block os_version_not_13 ;; esac
[ "$(hostname)" = "home-edge-01" ] || block hostname_mismatch
entry="$(getent passwd oleksii || true)"
[ -n "$entry" ] || block user_missing
IFS=: read -r user_name _ user_uid _ _ user_home _ <<EOF_USER
$entry
EOF_USER
[ "$user_name" = "oleksii" ] || block user_mismatch
[ "$user_uid" = "1000" ] || block uid_mismatch
[ "$user_home" = "/home/oleksii" ] || block home_mismatch
[ -d /run/user/1000 ] || block user_runtime_missing
[ -S /run/user/1000/bus ] || block user_dbus_missing
[ -n "$boot_id_before" ] || block boot_id_missing
ensure_private_state || block rollback_manifest_unavailable
for executable in "$GALLERY_REFRESH" "$GALLERY_VERIFY" "$BROTHER_VERIFY" "$AGGREGATE_VERIFY" "$REGISTRY_CLI" "$CAST_CONTROL" "$POINTER_STATUS" "$WATCHDOG_STATUS"; do
  validate_exec "$executable" || block fixed_executable_invalid
done
run_user "$REGISTRY_CLI" doctor >"$(bounded_log registry_doctor)" 2>&1 || block registry_doctor_failed
registry_status="healthy"
gallery_pre_log="$(bounded_log gallery_verify_pre)"
if run_user "$GALLERY_VERIFY" >"$gallery_pre_log" 2>&1; then
  gallery_status="already_healthy"
else
  run_user "$GALLERY_REFRESH" >"$(bounded_log gallery_refresh)" 2>&1 || block gallery_refresh_failed
  refresh_count=1
  gallery_post_log="$(bounded_log gallery_verify_post)"
  run_user "$GALLERY_VERIFY" >"$gallery_post_log" 2>&1 || block gallery_verify_failed
  gallery_status="repaired_healthy"
fi
gallery_count="$(extract_gallery_count "${gallery_post_log:-$gallery_pre_log}")"
[ "$gallery_count" -ge 0 ] 2>/dev/null || gallery_count=0
brother_log="$(bounded_log brother_verify)"
run_user "$BROTHER_VERIFY" >"$brother_log" 2>&1 || block brother_verify_failed
healthy_brother_json "$brother_log" || block brother_unhealthy
brother_status="healthy"
if run_user systemctl --user is-active --quiet "$CURRENT_BROTHER_SERVICE" >"$(bounded_log brother_service)" 2>&1; then
  current_brother_service_status="active"
else
  block brother_service_inactive
fi
if run_user systemctl --user is-active --quiet "$CURRENT_BROTHER_GUARD_TIMER" >"$(bounded_log brother_timer)" 2>&1; then
  current_brother_guard_timer_status="active"
else
  block brother_guard_timer_inactive
fi
aggregate_pre="$(bounded_log aggregate_verify_pre)"
if run_user "$AGGREGATE_VERIFY" >"$aggregate_pre" 2>&1; then
  aggregate_status="healthy"
else
  file_editable "$AGGREGATE_VERIFY" || block aggregate_source_not_editable
  [ "$(stat -c '%s' "$AGGREGATE_VERIFY")" -le 1048576 ] || block aggregate_source_too_large
  literal_count="$(grep -o "$STALE_AGGREGATE_UNIT_LITERAL" "$AGGREGATE_VERIFY" | wc -l | tr -d ' ')"
  [ "$literal_count" = "1" ] || block aggregate_literal_count_mismatch
  backup_file "$AGGREGATE_VERIFY" aggregate_source || block rollback_manifest_unavailable
  aggregate_tmp="$(mktemp)" || block aggregate_repair_failed
  python3 - "$AGGREGATE_VERIFY" "$STALE_AGGREGATE_UNIT_LITERAL" "$CURRENT_AGGREGATE_UNIT_LITERAL" >"$aggregate_tmp" <<'PY' || { rm -f "$aggregate_tmp"; fail_after_mutation aggregate_repair_failed; }
import sys
path, old, new = sys.argv[1:4]
data = open(path, "r", encoding="utf-8").read()
if data.count(old) != 1:
    sys.exit(1)
sys.stdout.write(data.replace(old, new, 1))
PY
  atomic_replace "$AGGREGATE_VERIFY" "$aggregate_tmp" || { rm -f "$aggregate_tmp"; fail_after_mutation aggregate_repair_failed; }
  rm -f "$aggregate_tmp"
  aggregate_source_repaired=true
  changed_file_count=$((changed_file_count + 1))
  if ! cmp -s "$AGGREGATE_VERIFY" "$ROLLBACK_DIR/files/${AGGREGATE_VERIFY#/}"; then :; else fail_after_mutation aggregate_no_byte_change; fi
  run_user "$AGGREGATE_VERIFY" >"$(bounded_log aggregate_verify_post)" 2>&1 || fail_after_mutation aggregate_verify_failed
  aggregate_status="healthy"
fi
run_user "$BROTHER_VERIFY" >"$(bounded_log brother_verify_post)" 2>&1 || fail_after_mutation brother_post_failed
healthy_brother_json "$(bounded_log brother_verify_post)" || fail_after_mutation brother_post_unhealthy
run_user systemctl --user is-active --quiet "$CURRENT_BROTHER_SERVICE" >"$(bounded_log brother_service_post)" 2>&1 || fail_after_mutation brother_service_post_inactive
run_user systemctl --user is-active --quiet "$CURRENT_BROTHER_GUARD_TIMER" >"$(bounded_log brother_timer_post)" 2>&1 || fail_after_mutation brother_timer_post_inactive
stale_before_count=0
while IFS= read -r -d '' scoped_path; do
  if grep -a -q "$OLD_HOME" "$scoped_path"; then
    stale_before_count=$((stale_before_count + 1))
    case "$scoped_path" in
      /home/oleksii/.config/systemd/user/*) kind="user_unit" ;;
      /etc/systemd/system/*) kind="system_unit" ;;
      *) kind="operational_file" ;;
    esac
    replace_stale_paths "$scoped_path" "$kind"
    result=$?
    [ "$result" = "0" ] || [ "$result" = "1" ] || fail_after_mutation stale_path_repair_failed
    [ "$kind" = "system_unit" ] && system_units_changed=true
    [ "$kind" = "user_unit" ] && user_units_changed=true
  fi
done < <(enumerate_scope)
[ "$system_units_changed" = true ] && manager_reload_for system
[ "$user_units_changed" = true ] && manager_reload_for user
stale_after_count=0
while IFS= read -r -d '' scoped_path; do
  if grep -a -q "$OLD_HOME" "$scoped_path"; then stale_after_count=$((stale_after_count + 1)); fi
done < <(enumerate_scope)
[ "$stale_after_count" = "0" ] || fail_after_mutation stale_operational_paths_remaining
run_user "$REGISTRY_CLI" doctor >"$(bounded_log registry_final)" 2>&1 || fail_after_mutation registry_final_failed
run_user "$GALLERY_VERIFY" >"$(bounded_log gallery_final)" 2>&1 || fail_after_mutation gallery_final_failed
run_user "$BROTHER_VERIFY" >"$(bounded_log brother_final)" 2>&1 || fail_after_mutation brother_final_failed
healthy_brother_json "$(bounded_log brother_final)" || fail_after_mutation brother_final_unhealthy
run_user "$AGGREGATE_VERIFY" >"$(bounded_log aggregate_final)" 2>&1 || fail_after_mutation aggregate_final_failed
run_user "$CAST_CONTROL" status >"$(bounded_log cast_status)" 2>&1 || fail_after_mutation cast_status_failed
healthy_cast_json "$(bounded_log cast_status)" || fail_after_mutation cast_unhealthy
cast_status="healthy"
run_user "$POINTER_STATUS" >"$(bounded_log pointer_status)" 2>&1 || fail_after_mutation pointer_status_failed
healthy_pointer_json "$(bounded_log pointer_status)" || fail_after_mutation pointer_unhealthy
pointer_status="healthy"
run_user "$WATCHDOG_STATUS" status >"$(bounded_log watchdog_status)" 2>&1 || fail_after_mutation watchdog_status_failed
watchdog_counts "$(bounded_log watchdog_status)" || fail_after_mutation watchdog_unhealthy
watchdog_status="healthy"
system_failed_unit_count="$(systemctl --failed --no-legend 2>/dev/null | wc -l | tr -d ' ')"
user_failed_unit_count="$(run_user systemctl --user --failed --no-legend 2>/dev/null | wc -l | tr -d ' ')"
[ "$system_failed_unit_count" = "0" ] || fail_after_mutation system_failed_units_present
[ "$user_failed_unit_count" = "0" ] || fail_after_mutation user_failed_units_present
run_user systemctl --user is-active --quiet "$CURRENT_BROTHER_SERVICE" >"$(bounded_log brother_service_final)" 2>&1 || fail_after_mutation brother_service_final_inactive
run_user systemctl --user is-active --quiet "$CURRENT_BROTHER_GUARD_TIMER" >"$(bounded_log brother_timer_final)" 2>&1 || fail_after_mutation brother_timer_final_inactive
current_brother_service_status="active"
current_brother_guard_timer_status="active"
[ "$boot_id_before" = "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)" ] || fail_after_mutation boot_id_changed
emit_receipt met
'''
