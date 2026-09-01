#!/data/data/com.termux/files/usr/bin/bash
set -u

REPO="alanua/Skeleton"
ISSUE="3653"
SSH_TARGET="hetzner-agent-runner-1"
REMOTE='set -eu
SUDO="/usr/bin/sudo -n"
SYSTEMCTL="/usr/bin/systemctl"
SERVICE="skeleton-runner-poll.service"
TIMER="skeleton-runner-poll.timer"

$SUDO $SYSTEMCTL daemon-reload
$SUDO $SYSTEMCTL reset-failed "$SERVICE" || true
$SUDO $SYSTEMCTL reset-failed "$TIMER" || true
$SUDO $SYSTEMCTL restart "$TIMER"
$SUDO $SYSTEMCTL is-enabled --quiet "$TIMER"
$SUDO $SYSTEMCTL is-active --quiet "$TIMER"
$SUDO $SYSTEMCTL start "$SERVICE"
$SUDO $SYSTEMCTL is-active --quiet "$TIMER"
printf "status=DONE\ntimer_enabled=true\ntimer_active=true\nservice_kick=done\n"'

status="BLOCKED"
reason="ssh_or_runner_recovery_failed"
remote_out=""

if remote_out="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_TARGET" "$REMOTE" 2>/dev/null)"; then
  if printf '%s\n' "$remote_out" | grep -Fxq 'status=DONE'; then
    status="DONE"
    reason="runner_timer_rearmed"
  else
    reason="remote_receipt_invalid"
  fi
fi

receipt=$(cat <<EOF
PHONE_RUNNER_RECOVERY_V1
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
