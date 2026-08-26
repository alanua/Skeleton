#!/usr/bin/env bash
set -Eeuo pipefail

DESTDIR=""
REPO_ROOT=""
EXPECTED_MAIN_SHA=""
RUNNER_USER="agent"
SSH_USER="skeleton-runner-gateway"
SSH_SHELL="/bin/sh"
SSH_PUBLIC_KEY=""
SSHD_BIN="/usr/sbin/sshd"
HARDENED_SYNTHETIC="${SKELETON_GATEWAY_HARDENED_SYNTHETIC:-0}"
CANONICAL_LIVE_REPO_ROOT="/home/agent/agent-dev/repos/Skeleton"
CANONICAL_ORIGIN_HTTPS="https://github.com/alanua/Skeleton.git"
CANONICAL_ORIGIN_SSH="git@github.com:alanua/Skeleton.git"
INSTALL_ROOT="/usr/local/lib/skeleton/runner-controller"
EXEC_ROOT="/usr/local/libexec/skeleton/runner-controller"
STATE_ROOT="/var/lib/skeleton/runner-controller"
SUDOERS_PATH="/etc/sudoers.d/skeleton-runner-controller-privileged-gateway"
SSHD_FRAGMENT="/etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf"
SSH_AUTHORIZED_KEYS="/var/lib/skeleton/runner-controller/ssh/authorized_keys"
GATEWAY_COMMAND="/usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway"

usage() {
  cat <<'EOF'
Usage: sudo scripts/install_runner_controller_privileged_gateway.sh --repo-root PATH --expected-main-sha SHA [--destdir PATH] [--ssh-public-key KEY] [--sshd-bin PATH]

Installs the Skeleton Runner privileged gateway from exact Git objects at the
approved main SHA. Live installs require the canonical runner-controller
Skeleton checkout and exact alanua/Skeleton origin before any root mutation.
DESTDIR runs the exact-object checks against a synthetic Git fixture. Set
SKELETON_GATEWAY_HARDENED_SYNTHETIC=1 for the production-like hardened fixture.
EOF
}

block() {
  printf 'BLOCKED: %s\n' "$1" >&2
  exit 2
}

target() {
  printf '%s%s\n' "$DESTDIR" "$1"
}

run_git() {
  git -C "$CANONICAL_REPO_ROOT" "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --destdir)
      DESTDIR="${2:?missing value for --destdir}"
      shift 2
      ;;
    --repo-root)
      REPO_ROOT="${2:?missing value for --repo-root}"
      shift 2
      ;;
    --expected-main-sha)
      EXPECTED_MAIN_SHA="${2:?missing value for --expected-main-sha}"
      shift 2
      ;;
    --ssh-public-key)
      SSH_PUBLIC_KEY="${2:?missing value for --ssh-public-key}"
      shift 2
      ;;
    --sshd-bin)
      SSHD_BIN="${2:?missing value for --sshd-bin}"
      shift 2
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

[[ -n "$REPO_ROOT" ]] || block "repo-root-required"
[[ -n "$EXPECTED_MAIN_SHA" ]] || block "expected-main-sha-required"
[[ "$EXPECTED_MAIN_SHA" =~ ^[0-9a-f]{40}$ ]] || block "expected-main-sha-invalid"
if [[ -z "$DESTDIR" && ${EUID:-$(id -u)} -ne 0 ]]; then
  block "installer-must-run-as-root"
fi
if [[ -z "$DESTDIR" && "$HARDENED_SYNTHETIC" != "0" ]]; then
  block "synthetic-mode-forbidden-live"
fi
if [[ "$HARDENED_SYNTHETIC" != "0" && "$HARDENED_SYNTHETIC" != "1" ]]; then
  block "synthetic-mode-invalid"
fi

CANONICAL_REPO_ROOT="$(realpath -e -- "$REPO_ROOT")" || block "repo-root-unavailable"
if [[ -z "$DESTDIR" && "$CANONICAL_REPO_ROOT" != "$CANONICAL_LIVE_REPO_ROOT" ]]; then
  block "repo-root-not-canonical-runner-controller-checkout"
fi

origin_url="$(run_git remote get-url origin 2>/dev/null)" || block "origin-unavailable"
if [[ "$origin_url" != "$CANONICAL_ORIGIN_HTTPS" && "$origin_url" != "$CANONICAL_ORIGIN_SSH" ]]; then
  if [[ -n "$DESTDIR" && "${SKELETON_GATEWAY_ALLOW_SYNTHETIC_ORIGIN:-}" == "1" ]]; then
    :
  else
    block "origin-mismatch"
  fi
fi

inside_work_tree="$(run_git rev-parse --is-inside-work-tree 2>/dev/null)" || block "repo-root-not-git-worktree"
[[ "$inside_work_tree" == "true" ]] || block "repo-root-not-git-worktree"

current_head="$(run_git rev-parse --verify HEAD^{commit})" || block "head-unavailable"
origin_main="$(run_git rev-parse --verify origin/main^{commit})" || block "origin-main-unavailable"
[[ "$current_head" == "$EXPECTED_MAIN_SHA" ]] || block "head-sha-mismatch"
[[ "$origin_main" == "$EXPECTED_MAIN_SHA" ]] || block "origin-main-sha-mismatch"
[[ -z "$(run_git status --porcelain=v1 --untracked-files=all)" ]] || block "worktree-not-clean"

fresh_remote_main="$(run_git ls-remote origin refs/heads/main | awk '{print $1}')" || block "fresh-remote-main-unavailable"
[[ "$fresh_remote_main" == "$EXPECTED_MAIN_SHA" ]] || block "fresh-remote-main-mismatch"

verify_tree_entry() {
  local mode="$1"
  local path="$2"
  local line
  line="$(run_git ls-tree "$EXPECTED_MAIN_SHA" -- "$path")" || block "tree-entry-unavailable-$path"
  [[ -n "$line" ]] || block "tree-entry-missing-$path"
  local actual_mode actual_type actual_blob actual_path
  actual_mode="${line%% *}"
  line="${line#* }"
  actual_type="${line%% *}"
  line="${line#* }"
  actual_blob="${line%%$'\t'*}"
  actual_path="${line#*$'\t'}"
  [[ "$actual_mode" == "$mode" && "$actual_type" == "blob" && "$actual_path" == "$path" ]] || block "tree-entry-mismatch-$path"
  [[ "$(run_git cat-file -t "$actual_blob")" == "blob" ]] || block "tree-blob-unavailable-$path"
  printf '%s\n' "$actual_blob"
}

verify_running_installer() {
  local script_path script_canonical script_relative expected_blob actual_blob
  script_path="${BASH_SOURCE[0]}"
  script_canonical="$(realpath -e -- "$script_path")" || block "installer-source-unavailable"
  case "$script_canonical" in
    "$CANONICAL_REPO_ROOT"/*) ;;
    *) block "installer-source-outside-repo" ;;
  esac
  script_relative="${script_canonical#"$CANONICAL_REPO_ROOT"/}"
  [[ "$script_relative" == "scripts/install_runner_controller_privileged_gateway.sh" ]] || block "installer-source-path-mismatch"
  expected_blob="$(verify_tree_entry 100755 "$script_relative")"
  actual_blob="$(run_git hash-object -- "$script_canonical")" || block "installer-hash-unavailable"
  [[ "$actual_blob" == "$expected_blob" ]] || block "installer-blob-mismatch"
}

copy_git_file() {
  local mode="$1"
  local source_path="$2"
  local dest_path="$3"
  local blob tmp
  blob="$(verify_tree_entry "$mode" "$source_path")"
  tmp="$(mktemp "$(dirname -- "$dest_path")/.git-object.XXXXXX")" || block "tempfile-unavailable"
  run_git cat-file -p "$blob" > "$tmp" || {
    rm -f -- "$tmp"
    block "git-object-read-failed-$source_path"
  }
  chmod "$mode_to_install" "$tmp"
  mv -f -- "$tmp" "$dest_path"
}

install_git_file() {
  local tree_mode="$1"
  local install_mode="$2"
  local source_path="$3"
  local absolute_dest="$4"
  local dest_path
  dest_path="$(target "$absolute_dest")"
  install -d -m 0755 "$(dirname -- "$dest_path")"
  mode_to_install="$install_mode" copy_git_file "$tree_mode" "$source_path" "$dest_path"
}

write_file() {
  local mode="$1"
  local absolute_dest="$2"
  local dest_path
  dest_path="$(target "$absolute_dest")"
  install -d -m 0755 "$(dirname -- "$dest_path")"
  cat > "$dest_path"
  chmod "$mode" "$dest_path"
}

validate_sshd_fragment() {
  local candidate="$1"
  [[ -x "$SSHD_BIN" ]] || block "sshd-binary-unavailable"
  "$SSHD_BIN" -t -f "$candidate" >/dev/null 2>&1 || return 1
  return 0
}

install_ssh_account_live() {
  [[ -x "$SSH_SHELL" ]] || block "ssh-shell-unavailable"
  if ! getent passwd "$SSH_USER" >/dev/null; then
    useradd --system --home-dir /nonexistent --shell "$SSH_SHELL" --no-create-home "$SSH_USER"
  fi
  passwd -l "$SSH_USER" >/dev/null 2>&1 || true
  usermod --home /nonexistent --shell "$SSH_SHELL" "$SSH_USER"
}

verify_effective_sshd_live() {
  local effective
  effective="$($SSHD_BIN -T -C user="$SSH_USER",host=localhost,addr=127.0.0.1 2>/dev/null)" || block "sshd-effective-config-unavailable"
  grep -Fxq "forcecommand $GATEWAY_COMMAND" <<<"$effective" || block "sshd-forcecommand-mismatch"
  grep -Fxq "permittty no" <<<"$effective" || block "sshd-permittty-mismatch"
  grep -Fxq "allowtcpforwarding no" <<<"$effective" || block "sshd-forwarding-mismatch"
  grep -Fxq "x11forwarding no" <<<"$effective" || block "sshd-x11-mismatch"
  grep -Fxq "allowagentforwarding no" <<<"$effective" || block "sshd-agent-forwarding-mismatch"
  grep -Fxq "permituserrc no" <<<"$effective" || block "sshd-userrc-mismatch"
  grep -Fxq "passwordauthentication no" <<<"$effective" || block "sshd-password-auth-mismatch"
  grep -Fxq "kbdinteractiveauthentication no" <<<"$effective" || block "sshd-kbd-auth-mismatch"
}

verify_running_installer

install -d -m 0755 "$(target "$INSTALL_ROOT/core")" "$(target "$INSTALL_ROOT/core/home_edge")" "$(target "$EXEC_ROOT")"
install -d -m 0700 "$(target "$STATE_ROOT")"
printf '%s\n' '# installed runner-controller gateway package' > "$(target "$INSTALL_ROOT/core/__init__.py")"
chmod 0444 "$(target "$INSTALL_ROOT/core/__init__.py")"
printf '%s\n' '# installed runner-controller gateway home-edge package' > "$(target "$INSTALL_ROOT/core/home_edge/__init__.py")"
chmod 0444 "$(target "$INSTALL_ROOT/core/home_edge/__init__.py")"

install_git_file 100644 0444 "core/runner_controller_privileged_gateway.py" "$INSTALL_ROOT/core/runner_controller_privileged_gateway.py"
install_git_file 100644 0444 "core/home_edge/esp_lab_stage1_signer_install.py" "$INSTALL_ROOT/core/home_edge/esp_lab_stage1_signer_install.py"
install_git_file 100755 0555 "scripts/runner_controller_privileged_gateway.py" "$EXEC_ROOT/privileged-gateway"
install_git_file 100644 0444 "RUNNER_PRIVILEGED_ACTIONS.yaml" "$INSTALL_ROOT/config/RUNNER_PRIVILEGED_ACTIONS.yaml"
install_git_file 100644 0444 "schemas/runner_controller_privileged_request.schema.json" "$INSTALL_ROOT/schemas/runner_controller_privileged_request.schema.json"
install_git_file 100644 0444 "schemas/runner_controller_privileged_receipt.schema.json" "$INSTALL_ROOT/schemas/runner_controller_privileged_receipt.schema.json"
install_git_file 100644 0444 "docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md" "$INSTALL_ROOT/docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md"

if [[ -z "$DESTDIR" || "$HARDENED_SYNTHETIC" == "1" ]]; then
  install_git_file 100644 0444 "core/runner_controller_privileged_gateway_hardening.py" "$INSTALL_ROOT/core/runner_controller_privileged_gateway_hardening.py"
  install_git_file 100644 0444 "CAPABILITY_REGISTRY.yaml" "$INSTALL_ROOT/config/CAPABILITY_REGISTRY.yaml"
else
  write_file 0444 "$INSTALL_ROOT/config/CAPABILITY_REGISTRY.yaml" <<'EOF'
version: "1.0.0"
capabilities:
  runner_controller_privileged_gateway:
    status: available
    module: core/runner_controller_privileged_gateway.py
    live_runtime_execution: true
    protected: true
    requires:
      - core/runner_controller_privileged_gateway.py
      - core/home_edge/esp_lab_stage1_signer_install.py
      - scripts/runner_controller_privileged_gateway.py
      - scripts/install_runner_controller_privileged_gateway.sh
      - RUNNER_PRIVILEGED_ACTIONS.yaml
      - schemas/runner_controller_privileged_request.schema.json
      - schemas/runner_controller_privileged_receipt.schema.json
      - docs/RUNNER_CONTROLLER_PRIVILEGED_GATEWAY.md
    tested: true
    added: "2026-08-25"
    description: Legacy synthetic-only fixture for pre-hardening compatibility tests.
EOF
fi

write_file 0444 "$INSTALL_ROOT/config/checkout.json" <<EOF
{"schema":"skeleton.runner_controller_checkout_config.v1","repository":"alanua/Skeleton","checkout_path":"$CANONICAL_LIVE_REPO_ROOT"}
EOF

write_file 0440 "$SUDOERS_PATH" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $EXEC_ROOT/privileged-gateway ""
EOF

if [[ -z "$SSH_PUBLIC_KEY" ]]; then
  printf 'DONE: Runner controller privileged gateway files installed inertly\n'
  printf 'gateway=%s\n' "$EXEC_ROOT/privileged-gateway"
  printf 'sudoers=%s\n' "$SUDOERS_PATH"
  printf 'ssh=DISABLED_NOT_CONFIGURED\n'
  exit 0
fi

case "$SSH_PUBLIC_KEY" in
  ssh-ed25519\ *|ecdsa-sha2-nistp256\ *) ;;
  *) block "unapproved-ssh-public-key-type" ;;
esac

if [[ -z "$DESTDIR" ]]; then
  install_ssh_account_live
elif [[ "$HARDENED_SYNTHETIC" == "1" ]]; then
  write_file 0444 "/etc/passwd.d/skeleton-runner-gateway.plan" <<EOF
$SSH_USER:x:synthetic:synthetic:Skeleton Runner Gateway:/nonexistent:$SSH_SHELL
EOF
else
  write_file 0444 "/etc/passwd.d/skeleton-runner-gateway.plan" <<EOF
$SSH_USER:x:synthetic:synthetic:Skeleton Runner Gateway:/nonexistent:/usr/sbin/nologin
EOF
fi

install -d -m 0700 "$(dirname -- "$(target "$SSH_AUTHORIZED_KEYS")")"
write_file 0600 "$SSH_AUTHORIZED_KEYS" <<EOF
command="$GATEWAY_COMMAND",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-user-rc $SSH_PUBLIC_KEY
EOF

write_file 0440 "/etc/sudoers.d/skeleton-runner-gateway" <<EOF
$SSH_USER ALL=(root) NOPASSWD: $EXEC_ROOT/privileged-gateway ""
EOF

install -d -m 0755 "$(dirname -- "$(target "$SSHD_FRAGMENT")")"
candidate="$(mktemp "$(target "$SSHD_FRAGMENT").candidate.XXXXXX")" || block "sshd-candidate-unavailable"
cat > "$candidate" <<EOF
Match User $SSH_USER
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile $SSH_AUTHORIZED_KEYS
    PermitTTY no
    AllowTcpForwarding no
    PermitOpen none
    PermitListen none
    X11Forwarding no
    AllowAgentForwarding no
    PermitUserRC no
    ForceCommand $GATEWAY_COMMAND
EOF
chmod 0444 "$candidate"
if ! validate_sshd_fragment "$candidate"; then
  rm -f -- "$candidate"
  block "sshd-validation-failed"
fi
mv -f -- "$candidate" "$(target "$SSHD_FRAGMENT")"

if [[ -z "$DESTDIR" ]]; then
  "$SSHD_BIN" -t >/dev/null 2>&1 || {
    rm -f -- "$SSHD_FRAGMENT"
    block "sshd-full-config-validation-failed"
  }
  if systemctl reload ssh.service >/dev/null 2>&1; then
    :
  elif systemctl reload sshd.service >/dev/null 2>&1; then
    :
  else
    block "sshd-reload-failed"
  fi
  verify_effective_sshd_live
fi

printf 'DONE: Runner controller privileged gateway files installed inertly\n'
printf 'gateway=%s\n' "$EXEC_ROOT/privileged-gateway"
printf 'sudoers=%s\n' "$SUDOERS_PATH"
printf 'ssh=READY\n'
printf 'ssh_user=%s\n' "$SSH_USER"
printf 'sshd_fragment=%s\n' "$SSHD_FRAGMENT"
