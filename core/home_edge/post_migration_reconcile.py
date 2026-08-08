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
IDEMPOTENCY_KEY = "home-edge-01-post-migration-reconcile-20260808-v1"
REQUEST_TIMEOUT_SECONDS = 900
RECEIPT_SCHEMA = "skeleton.home_edge.post_migration_reconcile_receipt.v1"
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

RECEIPT_FIELDS = (
    "maintenance_task_id",
    "os_identity_status",
    "node_identity_status",
    "registry_doctor_status",
    "gallery_pre_count",
    "gallery_post_count",
    "gallery_root_cause_class",
    "gallery_status",
    "brother_specialized_status",
    "aggregate_verifier_status",
    "stale_home_path_matches_before",
    "stale_home_path_matches_after",
    "cast_status",
    "pointer_status",
    "media_watchdog_status",
    "failed_units_count",
    "reboot_performed",
    "rollback_ready",
    "rollback_applied",
    "mutation_executor_receipt_hash",
    "final_postcheck_receipt_hash",
    "audit_receipt_hash",
    "stable_reason",
    "success_criteria",
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
    postcheck_status, postcheck_hash = _gateway_postcheck_status(environment=environment)
    public["final_postcheck_receipt_hash"] = postcheck_hash
    if postcheck_status != "ok":
        public["success_criteria"] = "not_met"
        if public.get("stable_reason") == "completed":
            public["stable_reason"] = "gateway_postcheck_blocked"
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
    return HomeEdgeExecRequest.from_mapping(
        {**request.to_mapping(include_signature=False), "signature": signature}
    )


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
    sanitized = _blocked_receipt("invalid_public_receipt")
    for field in RECEIPT_FIELDS:
        if field not in receipt:
            raise ValueError("receipt_field_missing")
    sanitized.clear()
    for field in RECEIPT_FIELDS:
        value = receipt[field]
        if isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            sanitized[field] = value
        elif isinstance(value, str) and _PUBLIC_VALUE_RE.fullmatch(value):
            sanitized[field] = value
        else:
            raise ValueError("receipt_field_not_public_safe")
    if sanitized["maintenance_task_id"] != TASK_ID:
        raise ValueError("receipt_task_id_mismatch")
    return sanitized


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [f"{field}={receipt[field]}" for field in RECEIPT_FIELDS]


def success_criteria_met(receipt: Mapping[str, object]) -> bool:
    return (
        receipt.get("success_criteria") == "met"
        and receipt.get("os_identity_status") == "verified"
        and receipt.get("node_identity_status") == "verified"
        and receipt.get("registry_doctor_status") == "healthy"
        and receipt.get("gallery_status") == "healthy"
        and receipt.get("brother_specialized_status") == "healthy"
        and receipt.get("aggregate_verifier_status") == "healthy"
        and receipt.get("stale_home_path_matches_after") == 0
        and receipt.get("cast_status") == "healthy"
        and receipt.get("pointer_status") == "healthy"
        and receipt.get("media_watchdog_status") == "healthy"
        and receipt.get("failed_units_count") == 0
        and receipt.get("reboot_performed") is False
        and receipt.get("rollback_ready") is True
    )


def _gateway_postcheck_status(
    *, environment: Mapping[str, str] | None = None
) -> tuple[str, str]:
    env = os.environ if environment is None else environment
    secret = env.get(EXEC_HMAC_SECRET_ENV, "")
    if not secret:
        return "missing_secret", "unavailable"
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-postcheck-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": "read_only",
            "argv": ["/usr/bin/true"],
            "timeout_seconds": 30,
            "operator_approval_ref": "root-read-only:home-edge-01-post-migration-reconcile-postcheck-v1",
            "idempotency_key": f"{IDEMPOTENCY_KEY}-postcheck-{uuid4()}",
            "run_as": "root",
            "mode": "argv",
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": f"{TASK_ID}-postcheck-{uuid4()}",
            "max_output_bytes": 1024,
        }
    )
    signed = HomeEdgeExecRequest.from_mapping(
        {
            **request.to_mapping(include_signature=False),
            "signature": sign_request(request, secret),
        }
    )
    try:
        receipt = execute_home_edge_request(signed.to_mapping())
    except Exception:
        return "blocked", "unavailable"
    return (
        "ok" if receipt.status == "ok" and receipt.exit_code == 0 else "blocked",
        receipt.receipt_hash,
    )


def _metadata_lines(body: str) -> list[str]:
    metadata = (body or "").split("```task", 1)[0]
    return [line for line in metadata.splitlines() if not line.lstrip().startswith("#")]


def _blocked_receipt(reason: str) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": TASK_ID,
        "os_identity_status": "unverified",
        "node_identity_status": "unverified",
        "registry_doctor_status": "blocked",
        "gallery_pre_count": 0,
        "gallery_post_count": 0,
        "gallery_root_cause_class": "not_diagnosed",
        "gallery_status": "blocked",
        "brother_specialized_status": "blocked",
        "aggregate_verifier_status": "blocked",
        "stale_home_path_matches_before": 0,
        "stale_home_path_matches_after": 0,
        "cast_status": "blocked",
        "pointer_status": "blocked",
        "media_watchdog_status": "blocked",
        "failed_units_count": 0,
        "reboot_performed": False,
        "rollback_ready": False,
        "rollback_applied": False,
        "mutation_executor_receipt_hash": "unavailable",
        "final_postcheck_receipt_hash": "unavailable",
        "audit_receipt_hash": "pending",
        "stable_reason": reason,
        "success_criteria": "not_met",
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
LOG_DIR=""
REFRESH="/home/oleksii/.local/bin/home-edge-screensaver-gallery-refresh"
GALLERY_VERIFY="/home/oleksii/.local/bin/home-edge-screensaver-verify-v9"
BROTHER_VERIFY="/home/oleksii/.local/bin/home-edge-brother-scankey-verify-v4"
AGGREGATE_VERIFY="/home/oleksii/.local/bin/home-edge-platform-verify"
GALLERY_ROOT="/home/oleksii/.cache/home-edge-screensaver"
STALENESS_ROOTS=(/home/oleksii/.config/skeleton /home/oleksii/.local/share/skeleton /home/oleksii/.cache/skeleton /var/lib/skeleton/home-edge-01 /etc/skeleton)
rollback_ready=false
rollback_applied=false
receipt_emitted=false
stable_reason="completed"
gallery_pre_count=0
gallery_post_count=0
gallery_root_cause_class="not_diagnosed"
stale_home_path_matches_before=0
stale_home_path_matches_after=0
boot_id_before="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
bounded_log() {
  local name="$1"
  if [ -n "$LOG_DIR" ]; then
    printf '%s/%s.log' "$LOG_DIR" "$name"
  else
    printf '/dev/null'
  fi
}
emit_receipt() {
  if [ "$receipt_emitted" = true ]; then exit 70; fi
  receipt_emitted=true
  local os_status="$1"
  local node_status="$2"
  local registry_status="$3"
  local gallery_status="$4"
  local brother_status="$5"
  local aggregate_status="$6"
  local cast_status="$7"
  local pointer_status="$8"
  local watchdog_status="$9"
  local failed_units="${10}"
  local criteria="${11}"
  local hash
  hash="$(printf '%s:%s:%s:%s:%s:%s:%s' "$TASK_ID" "$registry_status" "$gallery_status" "$brother_status" "$aggregate_status" "$stale_home_path_matches_after" "$stable_reason" | sha256sum | awk '{print $1}')"
  printf '{"maintenance_task_id":"%s",' "$TASK_ID"
  printf '"os_identity_status":"%s",' "$os_status"
  printf '"node_identity_status":"%s",' "$node_status"
  printf '"registry_doctor_status":"%s",' "$registry_status"
  printf '"gallery_pre_count":%s,' "$gallery_pre_count"
  printf '"gallery_post_count":%s,' "$gallery_post_count"
  printf '"gallery_root_cause_class":"%s",' "$gallery_root_cause_class"
  printf '"gallery_status":"%s",' "$gallery_status"
  printf '"brother_specialized_status":"%s",' "$brother_status"
  printf '"aggregate_verifier_status":"%s",' "$aggregate_status"
  printf '"stale_home_path_matches_before":%s,' "$stale_home_path_matches_before"
  printf '"stale_home_path_matches_after":%s,' "$stale_home_path_matches_after"
  printf '"cast_status":"%s",' "$cast_status"
  printf '"pointer_status":"%s",' "$pointer_status"
  printf '"media_watchdog_status":"%s",' "$watchdog_status"
  printf '"failed_units_count":%s,' "$failed_units"
  printf '"reboot_performed":false,'
  printf '"rollback_ready":%s,' "$rollback_ready"
  printf '"rollback_applied":%s,' "$rollback_applied"
  printf '"mutation_executor_receipt_hash":"pending",'
  printf '"final_postcheck_receipt_hash":"pending",'
  printf '"audit_receipt_hash":"%s",' "$hash"
  printf '"stable_reason":"%s",' "$stable_reason"
  printf '"success_criteria":"%s"' "$criteria"
  printf '}\n'
}
block() { stable_reason="$1"; emit_receipt blocked blocked blocked blocked blocked blocked blocked blocked blocked 0 not_met; exit 10; }
ensure_private_state() {
  install -d -m 0700 "$STATE_ROOT" "$ROLLBACK_ROOT" "$ROLLBACK_DIR" || return 1
  LOG_DIR="$ROLLBACK_DIR/logs"
  install -d -m 0700 "$LOG_DIR" || return 1
  : >"$MANIFEST" || return 1
  chmod 0600 "$MANIFEST" || return 1
}
safe_file_boundary() {
  case "$1" in
    /home/oleksii/.cache/home-edge-screensaver/*|/home/oleksii/.config/skeleton/*|/home/oleksii/.local/share/skeleton/*|/home/oleksii/.cache/skeleton/*|/var/lib/skeleton/home-edge-01/*|/etc/skeleton/*|/home/oleksii/.local/bin/home-edge-platform-verify) return 0 ;;
    *) return 1 ;;
  esac
}
backup_file() {
  local path="$1" rel backup mode uid gid
  safe_file_boundary "$path" || return 1
  rel="${path#/}"
  backup="$ROLLBACK_DIR/files/$rel"
  install -d -m 0700 "$(dirname "$backup")" || return 1
  if [ -e "$path" ]; then
    [ -f "$path" ] || return 1
    cp -a "$path" "$backup" || return 1
    chmod 0600 "$backup" || return 1
    mode="$(stat -c '%a' "$path" 2>/dev/null)" || return 1
    uid="$(stat -c '%u' "$path" 2>/dev/null)" || return 1
    gid="$(stat -c '%g' "$path" 2>/dev/null)" || return 1
    printf '%s\texisted\t%s\t%s\t%s\t%s\n' "$path" "$mode" "$uid" "$gid" "$backup" >>"$MANIFEST" || return 1
  else
    printf '%s\tcreated\t0\t0\t0\t-\n' "$path" >>"$MANIFEST" || return 1
  fi
  chmod 0600 "$MANIFEST" || return 1
  rollback_ready=true
  [ -r "$MANIFEST" ] || return 1
}
restore_files() {
  local path state mode uid gid backup failed=false
  while IFS="$(printf '\t')" read -r path state mode uid gid backup; do
    safe_file_boundary "$path" || { failed=true; continue; }
    if [ "$state" = "existed" ]; then
      install -d -m 0755 "$(dirname "$path")" || { failed=true; continue; }
      cp -a "$backup" "$path" || { failed=true; continue; }
      chown "$uid:$gid" "$path" 2>/dev/null || true
      chmod "$mode" "$path" 2>/dev/null || true
    elif [ "$state" = "created" ]; then
      rm -f -- "$path" || failed=true
    else
      failed=true
    fi
  done <"$MANIFEST"
  [ "$failed" = false ]
}
fail_after_mutation() {
  local reason="$1"
  if restore_files; then
    rollback_applied=true
    stable_reason="$reason"
    emit_receipt verified verified blocked blocked blocked blocked blocked blocked blocked 0 not_met
    exit 50
  fi
  stable_reason="rollback_failed"
  emit_receipt verified verified blocked blocked blocked blocked blocked blocked blocked 0 not_met
  exit 60
}
run_quiet() {
  local label="$1"
  shift
  "$@" >"$(bounded_log "$label")" 2>&1
}
count_gallery() {
  "$GALLERY_VERIFY" --json 2>/dev/null | awk -F: '/"qualified_count"/ {gsub(/[^0-9]/,"",$2); print $2; exit}'
}
gallery_health() {
  "$GALLERY_VERIFY" --json >"$(bounded_log gallery_verify)" 2>&1
}
classify_gallery_cause() {
  if [ "$gallery_pre_count" = "$gallery_post_count" ]; then
    gallery_root_cause_class="verifier_assumption"
  elif [ "$gallery_post_count" -gt "$gallery_pre_count" ]; then
    gallery_root_cause_class="stale_cache_state"
  elif grep -RIl '"asset_url"' "$GALLERY_ROOT" 2>/dev/null | xargs -r grep -qi 'duplicate'; then
    gallery_root_cause_class="duplicate_identity"
  elif grep -RIl '"asset_url"' "$GALLERY_ROOT" 2>/dev/null | xargs -r grep -qi 'broken'; then
    gallery_root_cause_class="broken_asset_metadata"
  else
    gallery_root_cause_class="unavailable_upstream_asset"
  fi
}
repair_gallery() {
  [ -x "$REFRESH" ] || block gallery_refresh_missing
  [ -x "$GALLERY_VERIFY" ] || block gallery_verifier_missing
  [ -d "$GALLERY_ROOT" ] || block gallery_boundary_missing
  gallery_pre_count="$(count_gallery)"
  case "$gallery_pre_count" in ''|*[!0-9]*) gallery_pre_count=0 ;; esac
  if [ "$gallery_pre_count" -ne 47 ] && [ "$gallery_pre_count" -ne 48 ]; then
    stable_reason="gallery_unexpected_count"
    emit_receipt verified verified healthy blocked blocked blocked blocked blocked blocked 0 not_met
    exit 11
  fi
  find "$GALLERY_ROOT" -maxdepth 2 -type f \( -name '*.json' -o -name '*.db' -o -name '*.sqlite' -o -name '*.state' \) -print0 2>/dev/null |
    while IFS= read -r -d '' file; do backup_file "$file" || exit 42; done
  [ "${PIPESTATUS[1]:-0}" = "0" ] || block rollback_manifest_unavailable
  run_quiet gallery_refresh "$REFRESH" || true
  gallery_post_count="$(count_gallery)"
  case "$gallery_post_count" in ''|*[!0-9]*) gallery_post_count=0 ;; esac
  classify_gallery_cause
  gallery_health || fail_after_mutation gallery_postcheck_failed
}
patch_aggregate_verifier() {
  [ -x "$BROTHER_VERIFY" ] || block brother_v4_verifier_missing
  run_quiet brother_v4 "$BROTHER_VERIFY" || block brother_v4_unhealthy
  [ -f "$AGGREGATE_VERIFY" ] || block aggregate_verifier_missing
  [ "$(stat -c '%u' "$AGGREGATE_VERIFY" 2>/dev/null)" = "1000" ] || [ "$(stat -c '%u' "$AGGREGATE_VERIFY" 2>/dev/null)" = "0" ] || block aggregate_verifier_ownership_unexpected
  if run_quiet aggregate_before "$AGGREGATE_VERIFY"; then
    return 0
  fi
  if ! grep -q 'home-edge-brother-scankey-verify' "$AGGREGATE_VERIFY"; then
    block aggregate_brother_check_missing
  fi
  if grep -q 'home-edge-brother-scankey-verify-v4' "$AGGREGATE_VERIFY"; then
    block aggregate_v4_unhealthy
  fi
  backup_file "$AGGREGATE_VERIFY" || block rollback_manifest_unavailable
  tmp="$ROLLBACK_DIR/aggregate.patch"
  sed 's#home-edge-brother-scankey-verify-v[0-3]#home-edge-brother-scankey-verify-v4#g; s#home-edge-brother-scankey-verify[^" '"'"'`]*#home-edge-brother-scankey-verify-v4#g' "$AGGREGATE_VERIFY" >"$tmp" || fail_after_mutation aggregate_patch_failed
  grep -q 'home-edge-brother-scankey-verify-v4' "$tmp" || fail_after_mutation aggregate_patch_failed
  if grep -q 'BROTHER_CHECK_DISABLED\|skip.*Brother\|bypass.*Brother' "$tmp"; then
    fail_after_mutation aggregate_patch_weakened
  fi
  cat "$tmp" >"$AGGREGATE_VERIFY" || fail_after_mutation aggregate_patch_failed
  chmod 0755 "$AGGREGATE_VERIFY" || fail_after_mutation aggregate_patch_failed
  run_quiet aggregate_after "$AGGREGATE_VERIFY" || fail_after_mutation aggregate_postcheck_failed
}
stale_scope_files() {
  find "${STALENESS_ROOTS[@]}" \
    \( -path '*/.ssh/*' -o -path '*/credentials/*' -o -path '*/secrets/*' -o -path '*/browser/*' -o -path '*/Browser/*' -o -path '*/Documents/*' -o -path '*/archives/*' -o -path '*/production-archives/*' \) -prune -o \
    -type f -size -1048576c \( -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.conf' -o -name '*.ini' -o -name '*.service' -o -name '*.timer' -o -name '*.sh' -o -name '*.py' \) -print 2>/dev/null
}
count_stale_paths() {
  stale_scope_files | xargs -r grep -Il '^.*\/home\/valertos08' 2>/dev/null | wc -l | tr -d ' '
}
replace_stale_paths() {
  stale_home_path_matches_before="$(count_stale_paths)"
  stale_scope_files | while IFS= read -r file; do
    grep -q '/home/valertos08' "$file" || continue
    target_refs="$(grep -o '/home/valertos08[^[:space:]\"'"'"'<>]*' "$file" | sort -u)"
    [ -n "$target_refs" ] || continue
    ok=true
    for old in $target_refs; do
      new="/home/oleksii${old#/home/valertos08}"
      [ -e "$new" ] || ok=false
    done
    [ "$ok" = true ] || continue
    backup_file "$file" || exit 42
    safe_file_boundary "$file" || exit 42
    sed 's#/home/valertos08#/home/oleksii#g' "$file" >"$ROLLBACK_DIR/stale.tmp" || exit 42
    cat "$ROLLBACK_DIR/stale.tmp" >"$file" || exit 42
  done
  [ "${PIPESTATUS[1]:-0}" = "0" ] || block rollback_manifest_unavailable
  stale_home_path_matches_after="$(count_stale_paths)"
}
persist_memorygate_conclusion() {
  conclusion="$STATE_ROOT/sanitized-conclusion.json"
  backup_file "$conclusion" || return 1
  printf '{"schema":"skeleton.home_edge.post_migration_reconcile.sanitized_conclusion.v1","task_id":"%s","gallery_root_cause_class":"%s","stale_home_path_matches_after":%s,"reboot_performed":false}\n' "$TASK_ID" "$gallery_root_cause_class" "$stale_home_path_matches_after" >"$conclusion" || return 1
  chmod 0600 "$conclusion" || return 1
}
postchecks() {
  registry_status=healthy
  run_quiet registry_doctor skeleton-devices doctor || registry_status=blocked
  gallery_status=healthy
  gallery_health || gallery_status=blocked
  brother_status=healthy
  run_quiet brother_final "$BROTHER_VERIFY" || brother_status=blocked
  aggregate_status=healthy
  run_quiet aggregate_final "$AGGREGATE_VERIFY" || aggregate_status=blocked
  cast_status=healthy
  run_quiet cast_check systemctl is-active skeleton-cast.service || cast_status=blocked
  pointer_status=healthy
  run_quiet pointer_service systemctl is-active home-edge-pointer-broker.service || pointer_status=blocked
  [ -S /run/home-edge-pointer-broker.sock ] || pointer_status=blocked
  [ -e /dev/uinput ] || pointer_status=blocked
  watchdog_status=healthy
  if ! run_quiet watchdog_check /home/oleksii/.local/bin/home-edge-media-watchdog-status --zero-critical --zero-warnings; then watchdog_status=blocked; fi
  failed_units="$(systemctl --failed --no-legend --plain 2>/dev/null | wc -l | tr -d ' ')"
  [ -n "$failed_units" ] || failed_units=0
  boot_id_after="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
  if [ "$boot_id_before" != "$boot_id_after" ]; then
    stable_reason="boot_id_changed"
    emit_receipt verified verified "$registry_status" "$gallery_status" "$brother_status" "$aggregate_status" "$cast_status" "$pointer_status" "$watchdog_status" "$failed_units" not_met
    exit 30
  fi
  if [ "$registry_status" = healthy ] && [ "$gallery_status" = healthy ] && [ "$brother_status" = healthy ] && [ "$aggregate_status" = healthy ] && [ "$cast_status" = healthy ] && [ "$pointer_status" = healthy ] && [ "$watchdog_status" = healthy ] && [ "$failed_units" = 0 ] && [ "$stale_home_path_matches_after" = 0 ]; then
    emit_receipt verified verified "$registry_status" "$gallery_status" "$brother_status" "$aggregate_status" "$cast_status" "$pointer_status" "$watchdog_status" "$failed_units" met
    exit 0
  fi
  fail_after_mutation postcheck_failed
}
[ "$(id -u)" = "0" ] || block missing_root_execution
[ -r /etc/os-release ] || block os_release_missing
. /etc/os-release
[ "${ID:-}" = "debian" ] || block os_not_debian
case "${VERSION_ID:-}" in 13*) ;; *) block os_version_not_13 ;; esac
[ "$(hostname)" = "home-edge-01" ] || block hostname_mismatch
oleksii_entry="$(getent passwd oleksii || true)"
[ -n "$oleksii_entry" ] || block user_missing
IFS=: read -r user_name _ user_uid _ _ user_home _ <<EOF_USER
$oleksii_entry
EOF_USER
[ "$user_name" = "oleksii" ] || block user_mismatch
[ "$user_uid" = "1000" ] || block uid_mismatch
[ "$user_home" = "/home/oleksii" ] || block home_mismatch
[ -n "$boot_id_before" ] || block boot_identity_unavailable
[ -x /usr/local/bin/home_edge_exec ] || block signed_gateway_path_missing
ensure_private_state || block rollback_manifest_unavailable
run_quiet registry_preflight skeleton-devices doctor || block registry_doctor_unhealthy
repair_gallery
patch_aggregate_verifier
replace_stale_paths
persist_memorygate_conclusion || fail_after_mutation memorygate_persist_failed
postchecks
'''
