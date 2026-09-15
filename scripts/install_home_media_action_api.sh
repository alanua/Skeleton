#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
REPLACE_KEY_STDIN=0
ENV_FILE="/etc/skeleton/home-media-action.env"
INSTALL_BIN="/usr/local/bin/skeleton-home-media-action-api"
SERVICE_FILE="/etc/systemd/system/skeleton-home-media-action.service"
BACKUP_DIR=""
COMMITTED=0

usage() {
  cat <<'EOF'
Usage:
  sudo scripts/install_home_media_action_api.sh [--repo-root PATH]
  API_KEY | sudo scripts/install_home_media_action_api.sh --replace-api-key-stdin [--repo-root PATH]

Installs a localhost-only bounded Action API and systemd service. It preserves an
existing API key unless --replace-api-key-stdin is used. When no key exists, a
new random key is generated and stored root-only. This script does not enable
public exposure; use enable_home_media_action_funnel.sh separately.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      REPO_ROOT="${2:?missing value for --repo-root}"
      shift 2
      ;;
    --replace-api-key-stdin)
      REPLACE_KEY_STDIN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  printf 'BLOCKED: installer must run as root\n' >&2
  exit 2
fi

required=(
  "$REPO_ROOT/scripts/home_edge_control_action_api.py"
  "$REPO_ROOT/scripts/home_edge_control_action_api_launcher.sh"
  "$REPO_ROOT/scripts/skeleton-home-media-action.service"
  "/etc/skeleton/home-edge-01.env"
  "/etc/skeleton/home-edge-executor-controller.env"
)
for path in "${required[@]}"; do
  if [[ ! -r "$path" ]]; then
    printf 'BLOCKED: required Action API input is unavailable\n' >&2
    exit 2
  fi
done

read_existing_key() {
  [[ -r "$ENV_FILE" ]] || return 1
  local line
  line="$(grep -E '^SKELETON_HOME_MEDIA_ACTION_API_KEY=' "$ENV_FILE" | tail -n 1 || true)"
  [[ -n "$line" ]] || return 1
  line="${line#SKELETON_HOME_MEDIA_ACTION_API_KEY=}"
  if [[ "$line" == \'*\' ]]; then
    line="${line:1:${#line}-2}"
  fi
  printf '%s' "$line"
}

quote_env() {
  local value="$1"
  printf "'%s'" "${value//\'/\'\"\'\"\'}"
}

api_key=""
if [[ $REPLACE_KEY_STDIN -eq 1 ]]; then
  api_key="$(cat)"
  api_key="${api_key%$'\n'}"
elif read_existing_key >/dev/null; then
  api_key="$(read_existing_key)"
else
  api_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
fi
if [[ ${#api_key} -lt 32 ]]; then
  printf 'BLOCKED: Action API key must contain at least 32 characters\n' >&2
  exit 2
fi

BACKUP_DIR="$(mktemp -d /tmp/skeleton-home-media-action.XXXXXX)"
rollback() {
  local rc=$?
  if [[ $COMMITTED -eq 1 ]]; then
    rm -rf "$BACKUP_DIR"
    return
  fi
  systemctl disable --now skeleton-home-media-action.service >/dev/null 2>&1 || true
  for target in "$ENV_FILE" "$INSTALL_BIN" "$SERVICE_FILE"; do
    name="$(basename "$target")"
    if [[ -e "$BACKUP_DIR/$name" ]]; then
      cp -a "$BACKUP_DIR/$name" "$target"
    else
      rm -f "$target"
    fi
  done
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf "$BACKUP_DIR"
  exit "$rc"
}
trap rollback EXIT

for target in "$ENV_FILE" "$INSTALL_BIN" "$SERVICE_FILE"; do
  if [[ -e "$target" ]]; then
    cp -a "$target" "$BACKUP_DIR/$(basename "$target")"
  fi
done

install -d -o root -g root -m 0700 /etc/skeleton
umask 077
printf 'SKELETON_HOME_MEDIA_ACTION_API_KEY=%s\n' "$(quote_env "$api_key")" > "$ENV_FILE"
chmod 0600 "$ENV_FILE"
chown root:root "$ENV_FILE"

install -o root -g root -m 0755 \
  "$REPO_ROOT/scripts/home_edge_control_action_api_launcher.sh" \
  "$INSTALL_BIN"
install -o root -g root -m 0644 \
  "$REPO_ROOT/scripts/skeleton-home-media-action.service" \
  "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable --now skeleton-home-media-action.service

for _ in $(seq 1 20); do
  if curl -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:8765/health >/dev/null; then
    break
  fi
  sleep 0.25
done
curl -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:8765/health >/dev/null

COMMITTED=1
rm -rf "$BACKUP_DIR"
trap - EXIT

printf 'DONE: bounded Home Media Action API installed on localhost\n'
printf 'api_key_file=%s\n' "$ENV_FILE"
printf 'next=enable Tailscale Funnel only after bounded API review\n'
