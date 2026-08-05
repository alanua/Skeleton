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


TASK_ID = "home_edge_01_debian_media_bootstrap_v1"
REPOSITORY = "alanua/Skeleton"
TARGET_NODE = "home-edge-01"
OPERATOR_APPROVAL = "EXPLICIT_FINISH_DEBIAN_MEDIA_NODE_20260805"
APPROVAL_REF = "home-edge-01-debian-media-bootstrap-v1"
IDEMPOTENCY_KEY = "home-edge-01-debian-media-bootstrap-v1"
REQUEST_TIMEOUT_SECONDS = 900
RECEIPT_SCHEMA = "skeleton.home_edge.debian_media_bootstrap_receipt.v1"
EXPECTED_MAIN_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

FIXED_PACKAGES = (
    "openssh-server",
    "sudo",
    "pipewire",
    "pipewire-pulse",
    "wireplumber",
    "alsa-utils",
    "mpv",
    "yt-dlp",
    "intel-media-va-driver",
    "vainfo",
    "chromium",
    "xserver-xorg",
    "lightdm",
    "openbox",
    "dbus-x11",
    "avahi-daemon",
    "curl",
    "jq",
    "git",
    "python3",
)

RECEIPT_FIELDS = (
    "maintenance_task_id",
    "os_identity_status",
    "node_identity_status",
    "reboot_guard_status",
    "reboot_performed",
    "packages_required_count",
    "packages_preexisting_count",
    "packages_added_count",
    "package_status",
    "display_manager_status",
    "autologin_status",
    "pipewire_status",
    "vaapi_status",
    "mpv_status",
    "chromium_status",
    "ssh_status",
    "gateway_postcheck_status",
    "physical_audio_status",
    "physical_video_status",
    "rollback_ready",
    "rollback_applied",
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


def execute_debian_media_bootstrap_task(
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
    request = build_bootstrap_request(environment=environment)
    receipt = execute_home_edge_request(request.to_mapping())
    public = public_receipt_from_executor_stdout(receipt.to_mapping())
    public["gateway_postcheck_status"] = _gateway_postcheck_status(environment=environment)
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


def build_bootstrap_request(
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
            "script": BOOTSTRAP_SCRIPT,
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
        and receipt.get("package_status") == "installed"
        and receipt.get("autologin_status") == "configured"
        and receipt.get("reboot_performed") is False
        and receipt.get("physical_audio_status") == "physical_pending"
        and receipt.get("physical_video_status") == "physical_pending"
        and receipt.get("gateway_postcheck_status") == "ok"
    )


def _gateway_postcheck_status(*, environment: Mapping[str, str] | None = None) -> str:
    env = os.environ if environment is None else environment
    secret = env.get(EXEC_HMAC_SECRET_ENV, "")
    if not secret:
        return "missing_secret"
    request = HomeEdgeExecRequest.from_mapping(
        {
            "request_id": f"{TASK_ID}-postcheck-{uuid4()}",
            "node_id": TARGET_NODE,
            "execution_lane": "read_only",
            "argv": ["/usr/bin/true"],
            "timeout_seconds": 30,
            "operator_approval_ref": "root-read-only:home-edge-01-debian-media-bootstrap-postcheck-v1",
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
    except Exception:  # noqa: BLE001 - public report must be stable.
        return "blocked"
    return "ok" if receipt.status == "ok" and receipt.exit_code == 0 else "blocked"


def _metadata_lines(body: str) -> list[str]:
    metadata = (body or "").split("```task", 1)[0]
    return [line for line in metadata.splitlines() if not line.lstrip().startswith("#")]


def _blocked_receipt(reason: str) -> dict[str, object]:
    receipt: dict[str, object] = {
        "maintenance_task_id": TASK_ID,
        "os_identity_status": "unverified",
        "node_identity_status": "unverified",
        "reboot_guard_status": "unverified",
        "reboot_performed": False,
        "packages_required_count": len(FIXED_PACKAGES),
        "packages_preexisting_count": 0,
        "packages_added_count": 0,
        "package_status": "blocked",
        "display_manager_status": "blocked",
        "autologin_status": "blocked",
        "pipewire_status": "blocked",
        "vaapi_status": "blocked",
        "mpv_status": "blocked",
        "chromium_status": "blocked",
        "ssh_status": "blocked",
        "gateway_postcheck_status": "blocked",
        "physical_audio_status": "physical_pending",
        "physical_video_status": "physical_pending",
        "rollback_ready": True,
        "rollback_applied": False,
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


BOOTSTRAP_SCRIPT = r'''set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
TASK_ID="home_edge_01_debian_media_bootstrap_v1"
MARKER="/var/lib/skeleton/home-edge-01/debian-media-bootstrap-v1.complete"
PACKAGES=(openssh-server sudo pipewire pipewire-pulse wireplumber alsa-utils mpv yt-dlp intel-media-va-driver vainfo chromium xserver-xorg lightdm openbox dbus-x11 avahi-daemon curl jq git python3)
GUARDS=(skeleton-home-edge-first-boot-guard.service skeleton-home-edge-first-boot-guard.timer home-edge-first-boot-guard.service home-edge-first-boot-guard.timer)
guard_status="not_present"
rollback_applied=false
packages_preexisting=0
packages_added=0
boot_id_before="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
stable_reason="completed"
json_escape() { printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()), end="")'; }
emit_receipt() {
  local success="$1"
  local package_status="$2"
  local display_status="$3"
  local autologin_status="$4"
  local pipewire_status="$5"
  local vaapi_status="$6"
  local mpv_status="$7"
  local chromium_status="$8"
  local ssh_status="$9"
  local criteria="${10}"
  local hash
  hash="$(printf '%s:%s:%s:%s:%s:%s' "$TASK_ID" "$package_status" "$display_status" "$autologin_status" "$pipewire_status" "$stable_reason" | sha256sum | awk '{print $1}')"
  printf '{'
  printf '"maintenance_task_id":'; json_escape "$TASK_ID"; printf ','
  printf '"os_identity_status":'; json_escape "$success"; printf ','
  printf '"node_identity_status":'; json_escape "$success"; printf ','
  printf '"reboot_guard_status":'; json_escape "$guard_status"; printf ','
  printf '"reboot_performed":false,'
  printf '"packages_required_count":%s,' "${#PACKAGES[@]}"
  printf '"packages_preexisting_count":%s,' "$packages_preexisting"
  printf '"packages_added_count":%s,' "$packages_added"
  printf '"package_status":'; json_escape "$package_status"; printf ','
  printf '"display_manager_status":'; json_escape "$display_status"; printf ','
  printf '"autologin_status":'; json_escape "$autologin_status"; printf ','
  printf '"pipewire_status":'; json_escape "$pipewire_status"; printf ','
  printf '"vaapi_status":'; json_escape "$vaapi_status"; printf ','
  printf '"mpv_status":'; json_escape "$mpv_status"; printf ','
  printf '"chromium_status":'; json_escape "$chromium_status"; printf ','
  printf '"ssh_status":'; json_escape "$ssh_status"; printf ','
  printf '"gateway_postcheck_status":"pending",'
  printf '"physical_audio_status":"physical_pending",'
  printf '"physical_video_status":"physical_pending",'
  printf '"rollback_ready":true,'
  printf '"rollback_applied":%s,' "$rollback_applied"
  printf '"audit_receipt_hash":'; json_escape "$hash"; printf ','
  printf '"stable_reason":'; json_escape "$stable_reason"; printf ','
  printf '"success_criteria":'; json_escape "$criteria"
  printf '}\n'
}
block() { stable_reason="$1"; emit_receipt blocked blocked blocked blocked blocked blocked blocked blocked blocked not_met; exit 10; }
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
[ -w / ] || block root_not_writable
free_kib="$(df -Pk / | awk 'NR==2 {print $4}')"
[ "${free_kib:-0}" -ge 8388608 ] || block free_space_low
[ "$(findmnt -n -o FSTYPE / 2>/dev/null || true)" != "overlay" ] || block external_live_root_ambiguous
[ ! -e /var/lib/dpkg/lock-frontend ] || ! fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || block dpkg_lock_active
[ ! -e /var/lib/dpkg/lock ] || ! fuser /var/lib/dpkg/lock >/dev/null 2>&1 || block dpkg_lock_active
[ "$(findmnt -n -o OPTIONS / | grep -c '(^|,)ro(,|$)' || true)" = "0" ] || block filesystem_read_only
systemctl list-units --type=service --type=timer --all --no-legend >/dev/null 2>&1 || block executor_identity_unavailable
if [ -f "$MARKER" ]; then
  for pkg in "${PACKAGES[@]}"; do dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -qx "install ok installed" && packages_preexisting=$((packages_preexisting+1)); done
  stable_reason="already_complete"
  emit_receipt verified installed service_active configured "$(systemctl --user -M oleksii@ status pipewire.service >/dev/null 2>&1 && echo session_ready || echo pending_session)" "$(test -e /dev/dri/renderD128 && echo configured || echo physical_pending)" "$(mpv --version >/dev/null 2>&1 && echo configured || echo blocked)" "$(chromium --version >/dev/null 2>&1 && echo configured || echo blocked)" "$(systemctl is-enabled ssh >/dev/null 2>&1 && echo service_active || echo pending_service)" met
  exit 0
fi
for unit in "${GUARDS[@]}"; do
  if systemctl list-unit-files "$unit" --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$unit"; then
    fragment="$(systemctl show -p FragmentPath --value "$unit" 2>/dev/null || true)"
    exec_start="$(systemctl show -p ExecStart --value "$unit" 2>/dev/null || true)"
    if { [ -n "$fragment" ] && grep -q 'skeleton.home_edge.debian13.first_boot_guard.v1' "$fragment"; } || printf '%s' "$exec_start" | grep -q 'skeleton.home_edge.debian13.first_boot_guard.v1'; then
      systemctl disable "$unit" >/dev/null 2>&1 || true
      guard_status="disabled_verified"
    else
      guard_status="unverified"
    fi
  fi
done
preexisting_file="$(mktemp)"
added_file="$(mktemp)"
backup_dir="$(mktemp -d)"
cleanup() { rm -f "$preexisting_file" "$added_file"; rm -rf "$backup_dir"; }
trap cleanup EXIT
for pkg in "${PACKAGES[@]}"; do
  if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -qx "install ok installed"; then
    printf '%s\n' "$pkg" >>"$preexisting_file"
  fi
done
packages_preexisting="$(wc -l <"$preexisting_file" | tr -d ' ')"
backup_file() {
  local file="$1"
  if [ -e "$file" ]; then
    mkdir -p "$backup_dir/$(dirname "$file")"
    cp -a "$file" "$backup_dir/$file"
    chmod go-rwx "$backup_dir/$file" || true
  fi
}
restore_backups() {
  for backup in $(find "$backup_dir" -type f 2>/dev/null); do
    target="${backup#$backup_dir}"
    cp -a "$backup" "$target"
  done
}
rollback_new_packages() {
  if [ -s "$added_file" ]; then
    xargs -r apt-get remove -y --purge <"$added_file" >/dev/null 2>&1 || true
    rollback_applied=true
  fi
}
if ! apt-get update; then stable_reason="apt_update_failed"; emit_receipt verified blocked blocked blocked blocked blocked blocked blocked blocked not_met; exit 20; fi
if ! apt-get install -y --no-install-recommends "${PACKAGES[@]}"; then
  for pkg in "${PACKAGES[@]}"; do
    if ! grep -qx "$pkg" "$preexisting_file" && dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -qx "install ok installed"; then printf '%s\n' "$pkg" >>"$added_file"; fi
  done
  rollback_new_packages
  restore_backups
  stable_reason="apt_install_failed"
  emit_receipt verified failed blocked blocked blocked blocked blocked blocked blocked not_met
  exit 30
fi
for pkg in "${PACKAGES[@]}"; do
  if ! grep -qx "$pkg" "$preexisting_file" && dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -qx "install ok installed"; then printf '%s\n' "$pkg" >>"$added_file"; fi
done
packages_added="$(sort -u "$added_file" | wc -l | tr -d ' ')"
for group in audio video render input; do getent group "$group" >/dev/null 2>&1 && usermod -aG "$group" oleksii; done
backup_file /etc/lightdm/lightdm.conf.d/50-skeleton-home-edge-autologin.conf
mkdir -p /etc/lightdm/lightdm.conf.d
cat >/etc/lightdm/lightdm.conf.d/50-skeleton-home-edge-autologin.conf <<'EOF_LIGHTDM'
[Seat:*]
autologin-user=oleksii
autologin-user-timeout=0
user-session=openbox
EOF_LIGHTDM
chmod 0644 /etc/lightdm/lightdm.conf.d/50-skeleton-home-edge-autologin.conf
install -d -m 0755 -o 1000 -g 1000 /home/oleksii/.config/openbox /home/oleksii/.config/mpv /etc/chromium/policies/managed
backup_file /home/oleksii/.config/openbox/autostart
cat >/home/oleksii/.config/openbox/autostart <<'EOF_OPENBOX'
pipewire &
pipewire-pulse &
wireplumber &
xsetroot -solid black
EOF_OPENBOX
chown 1000:1000 /home/oleksii/.config/openbox/autostart
chmod 0755 /home/oleksii/.config/openbox/autostart
backup_file /home/oleksii/.config/mpv/mpv.conf
cat >/home/oleksii/.config/mpv/mpv.conf <<'EOF_MPV'
hwdec=auto-safe
ao=pipewire
EOF_MPV
chown 1000:1000 /home/oleksii/.config/mpv/mpv.conf
chmod 0644 /home/oleksii/.config/mpv/mpv.conf
backup_file /etc/chromium/policies/managed/skeleton-home-edge-media.json
cat >/etc/chromium/policies/managed/skeleton-home-edge-media.json <<'EOF_CHROMIUM'
{"HardwareAccelerationModeEnabled":true}
EOF_CHROMIUM
chmod 0644 /etc/chromium/policies/managed/skeleton-home-edge-media.json
for service in ssh lightdm avahi-daemon; do systemctl list-unit-files "$service.service" --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$service.service" && systemctl enable "$service.service" >/dev/null 2>&1 || true; done
pipewire_status="pending_session"
if [ -d /run/user/1000 ]; then
  systemctl --user -M oleksii@ enable pipewire.service pipewire-pulse.service wireplumber.service >/dev/null 2>&1 && pipewire_status="session_ready" || pipewire_status="pending_session"
fi
package_status="installed"
for pkg in "${PACKAGES[@]}"; do dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -qx "install ok installed" || package_status="failed"; done
if [ "$package_status" != "installed" ]; then rollback_new_packages; restore_backups; stable_reason="package_parity_failed"; emit_receipt verified failed blocked blocked "$pipewire_status" blocked blocked blocked blocked not_met; exit 40; fi
ssh_status="$(systemctl is-enabled ssh >/dev/null 2>&1 && systemctl is-active ssh >/dev/null 2>&1 && echo service_active || echo pending_service)"
display_status="$(systemctl is-enabled lightdm >/dev/null 2>&1 && echo service_active || echo pending_service)"
autologin_status="$(grep -qx 'autologin-user=oleksii' /etc/lightdm/lightdm.conf.d/50-skeleton-home-edge-autologin.conf && grep -qx 'user-session=openbox' /etc/lightdm/lightdm.conf.d/50-skeleton-home-edge-autologin.conf && echo configured || echo blocked)"
mpv_status="$(mpv --version >/dev/null 2>&1 && echo configured || echo blocked)"
chromium_status="$(chromium --version >/dev/null 2>&1 && echo configured || echo blocked)"
vaapi_status="$(test -e /dev/dri/renderD128 && vainfo >/dev/null 2>&1 && echo configured || echo physical_pending)"
pactl list short sinks 2>/dev/null | grep -q 'alsa_output.pci-0000_00_1f.3.analog-stereo' || true
if [ "$boot_id_before" != "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)" ]; then
  rollback_new_packages
  restore_backups
  stable_reason="boot_id_changed"
  emit_receipt verified "$package_status" "$display_status" "$autologin_status" "$pipewire_status" "$vaapi_status" "$mpv_status" "$chromium_status" "$ssh_status" not_met
  exit 50
fi
if [ "$autologin_status" != "configured" ] || [ "$mpv_status" = "blocked" ] || [ "$chromium_status" = "blocked" ]; then
  rollback_new_packages
  restore_backups
  stable_reason="verification_failed"
  emit_receipt verified "$package_status" "$display_status" "$autologin_status" "$pipewire_status" "$vaapi_status" "$mpv_status" "$chromium_status" "$ssh_status" not_met
  exit 50
fi
install -d -m 0700 /var/lib/skeleton/home-edge-01
printf 'skeleton.home_edge.debian_media_bootstrap.v1\n' >"$MARKER"
chmod 0600 "$MARKER"
emit_receipt verified "$package_status" "$display_status" "$autologin_status" "$pipewire_status" "$vaapi_status" "$mpv_status" "$chromium_status" "$ssh_status" met
'''
