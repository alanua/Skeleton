#!/data/data/com.termux/files/usr/bin/bash
set -u

REPO="alanua/Skeleton"
ISSUE="3653"
SSH_TARGET="hetzner-agent-runner-1"
TMP_ERR="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/skeleton_runner_recovery_ssh.err"
trap 'rm -f "$TMP_ERR"' EXIT

REMOTE='set -u
SUDO="/usr/bin/sudo -n"
SYSTEMCTL="/usr/bin/systemctl"
SERVICE="skeleton-runner-poll.service"
TIMER="skeleton-runner-poll.timer"

if ! $SUDO true >/dev/null 2>&1; then
  printf "status=BLOCKED\nreason=sudo_nopasswd_unavailable\n"
  exit 21
fi
if ! $SUDO $SYSTEMCTL cat "$TIMER" >/dev/null 2>&1; then
  printf "status=BLOCKED\nreason=canonical_timer_missing\n"
  exit 22
fi
if ! $SUDO $SYSTEMCTL cat "$SERVICE" >/dev/null 2>&1; then
  printf "status=BLOCKED\nreason=canonical_service_missing\n"
  exit 23
fi
if ! $SUDO $SYSTEMCTL daemon-reload >/dev/null 2>&1; then
  printf "status=BLOCKED\nreason=daemon_reload_failed\n"
  exit 24
fi
$SUDO $SYSTEMCTL reset-failed "$SERVICE" >/dev/null 2>&1 || true
$SUDO $SYSTEMCTL reset-failed "$TIMER" >/dev/null 2>&1 || true
if ! $SUDO $SYSTEMCTL restart "$TIMER" >/dev/null 2>&1; then
  printf "status=BLOCKED\nreason=timer_restart_failed\n"
  exit 25
fi
if ! $SUDO $SYSTEMCTL is-enabled --quiet "$TIMER"; then
  printf "status=BLOCKED\nreason=timer_not_enabled\n"
  exit 26
fi
if ! $SUDO $SYSTEMCTL is-active --quiet "$TIMER"; then
  printf "status=BLOCKED\nreason=timer_not_active\n"
  exit 27
fi
if ! $SUDO $SYSTEMCTL start "$SERVICE" >/dev/null 2>&1; then
  printf "status=BLOCKED\nreason=service_kick_failed\n"
  exit 28
fi
if ! $SUDO $SYSTEMCTL is-active --quiet "$TIMER"; then
  printf "status=BLOCKED\nreason=timer_inactive_after_service_kick\n"
  exit 29
fi
printf "status=DONE\nreason=runner_timer_rearmed\n"
'

status="BLOCKED"
reason="ssh_transport_failed"
remote_out=""
ssh_rc=0

remote_out="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_TARGET" "$REMOTE" 2>"$TMP_ERR")" || ssh_rc=$?

if [ "$ssh_rc" -eq 0 ]; then
  if printf '%s\n' "$remote_out" | grep -Fxq 'status=DONE'; then
    status="DONE"
    reason="runner_timer_rearmed"
  elif printf '%s\n' "$remote_out" | grep -q '^reason='; then
    reason="$(printf '%s\n' "$remote_out" | sed -n 's/^reason=//p' | head -n1)"
  else
    reason="remote_receipt_invalid"
  fi
else
  if grep -qi 'Could not resolve hostname\|Name or service not known\|Temporary failure in name resolution' "$TMP_ERR" 2>/dev/null; then
    reason="ssh_target_unresolvable"
  elif grep -qi 'Permission denied' "$TMP_ERR" 2>/dev/null; then
    reason="ssh_auth_failed"
  elif grep -qi 'Host key verification failed\|REMOTE HOST IDENTIFICATION HAS CHANGED' "$TMP_ERR" 2>/dev/null; then
    reason="ssh_hostkey_failed"
  elif grep -qi 'Connection timed out\|Operation timed out' "$TMP_ERR" 2>/dev/null; then
    reason="ssh_timeout"
  elif grep -qi 'Connection refused' "$TMP_ERR" 2>/dev/null; then
    reason="ssh_connection_refused"
  elif printf '%s\n' "$remote_out" | grep -q '^reason='; then
    reason="$(printf '%s\n' "$remote_out" | sed -n 's/^reason=//p' | head -n1)"
  fi
fi

receipt=$(cat <<EOF
PHONE_RUNNER_RECOVERY_V2
status=${status}
reason=${reason}
issue=${ISSUE}
private_host_metadata_exposed=false
secrets_exposed=false
runtime_scope=runner_timer_and_single_service_kick
EOF
)

if command -v gh >/dev/null 2>&1; then
  gh issue comment "$ISSUE" --repo "$REPO" --body "$receipt" >/dev/null 2>&1 || true
fi

printf '%s\n' "$ISSUE"
exit 0
