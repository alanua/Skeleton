#!/usr/bin/env bash
set -euo pipefail
umask 077
TARGET="192.168.1.70"
EXPECTED_HOST_FP="SHA256:82ywZgv10JnABpQXMF3as+i1a5YZSwylFiiPwDmEs2E"
CONTROLLER_FP="SHA256:XMBDn1WczsO4o/xkZgTalSPY+zqXudWcS2Ix7EozzBU"
CONTROLLER_PUB='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICJDcoiwjNxJbG7Weurg+H+o29iUq9D8Sg+6OAmJr1qK skeleton-controller:home_edge_01->mikrotik_home:v1'

fail() { printf 'RESULT=ERROR\nREASON=%s\n=== EXIT: ERROR ===\n' "$1"; exit 2; }
for c in ssh ssh-keyscan ssh-keygen; do command -v "$c" >/dev/null 2>&1 || fail "missing_$c"; done
if command -v openssl >/dev/null 2>&1; then
  BOOTSTRAP_PASSWORD="$(openssl rand -hex 24)"
elif command -v python3 >/dev/null 2>&1; then
  BOOTSTRAP_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
else
  fail 'missing_random_generator'
fi

read -r -p 'MikroTik admin username: ' ADMIN_USER
[ -n "$ADMIN_USER" ] || fail 'admin_username_empty'

KH="$(mktemp)"; CMDS="$(mktemp)"; OUT="$(mktemp)"
trap 'rm -f "$KH" "$CMDS" "$OUT"' EXIT
ssh-keyscan -T 5 "$TARGET" 2>/dev/null > "$KH" || true
[ -s "$KH" ] || fail 'router_unreachable'
if ! ssh-keygen -lf "$KH" | awk '{print $2}' | grep -Fxq "$EXPECTED_HOST_FP"; then fail 'host_fingerprint_mismatch'; fi

cat > "$CMDS" <<ROS
:do {
  :if ([:len [/user group find where name="skeleton-ops"]] = 0) do={/user group add name="skeleton-ops" policy=ssh,read,write,test} else={/user group set [find where name="skeleton-ops"] policy=ssh,read,write,test}
  :if ([:len [/user find where name="skeleton"]] = 0) do={/user add name="skeleton" group="skeleton-ops" password="$BOOTSTRAP_PASSWORD"} else={/user set [find where name="skeleton"] group="skeleton-ops" password="$BOOTSTRAP_PASSWORD"}
  :if ([:len [/file find where name="skeleton-home-edge.pub"]] = 0) do={/file add name="skeleton-home-edge.pub" type=file}
  /file set [find where name="skeleton-home-edge.pub"] contents="$CONTROLLER_PUB"
  :if ([:len [/user ssh-keys find where user="skeleton"]] = 0) do={/user ssh-keys import public-key-file=skeleton-home-edge.pub user=skeleton}
  /file remove [find where name="skeleton-home-edge.pub"]
  :put "SKELETON_ROUTER_ENROLL=OK"
} on-error={:put "SKELETON_ROUTER_ENROLL=ERROR"; :error "skeleton enrollment failed"}
ROS
chmod 600 "$CMDS"
ssh -T -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive -o PasswordAuthentication=yes -o KbdInteractiveAuthentication=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KH" "$ADMIN_USER@$TARGET" < "$CMDS" | tee "$OUT"
grep -q 'SKELETON_ROUTER_ENROLL=OK' "$OUT" || fail 'router_script_failed'
printf 'RESULT=SUCCESS\nDEVICE=mikrotik_home\nTARGET=%s\nHOST_FINGERPRINT=%s\nCONTROLLER_FINGERPRINT=%s\nNEXT=WAIT_FOR_HOME_EDGE_CANARY\n=== EXIT: SUCCESS ===\n' "$TARGET" "$EXPECTED_HOST_FP" "$CONTROLLER_FP"
