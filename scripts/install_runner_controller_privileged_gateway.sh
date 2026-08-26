#!/bin/bash
set -Eeuo pipefail
PATH="/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
unset BASH_ENV ENV CDPATH

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
PROTECTED_BOOTSTRAP_PATH="/usr/local/libexec/skeleton/runner-controller/bootstrap/install_runner_controller_privileged_gateway.sh"
BOOTSTRAP_SOURCE_PATH="scripts/install_runner_controller_privileged_gateway.sh"

GIT_BIN="/usr/bin/git"
RUNUSER_BIN="/usr/sbin/runuser"
ENV_BIN="/usr/bin/env"
VISUDO_BIN="/usr/sbin/visudo"
STAT_BIN="/usr/bin/stat"
MAX_GIT_OUTPUT_BYTES=65536
MAX_SSH_KEY_BYTES=4096

INSTALL_ROOT="/usr/local/lib/skeleton/runner-controller"
EXEC_ROOT="/usr/local/libexec/skeleton/runner-controller"
STATE_ROOT="/var/lib/skeleton/runner-controller"
SUDOERS_PATH="/etc/sudoers.d/skeleton-runner-controller-privileged-gateway"
SSH_SUDOERS_PATH="/etc/sudoers.d/skeleton-runner-gateway"
SSHD_FRAGMENT="/etc/ssh/sshd_config.d/skeleton-runner-controller-privileged-gateway.conf"
SSH_AUTHORIZED_KEYS="/var/lib/skeleton/runner-controller/ssh/authorized_keys"
GATEWAY_COMMAND="/usr/bin/sudo -n /usr/local/libexec/skeleton/runner-controller/privileged-gateway"

usage() {
  cat <<'EOF'
Usage: install_runner_controller_privileged_gateway.sh --repo-root PATH --expected-main-sha SHA [--destdir PATH] [--ssh-public-key KEY] [--sshd-bin PATH]

Live root execution is accepted only from the protected bootstrap copy:
  /usr/local/libexec/skeleton/runner-controller/bootstrap/install_runner_controller_privileged_gateway.sh

DESTDIR is test-only synthetic mode and never relaxes live root authority.
EOF
}

block() {
  printf 'BLOCKED: %s\n' "$1" >&2
  exit 2
}

target() {
  printf '%s%s\n' "$DESTDIR" "$1"
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
if [[ -z "$DESTDIR" && "$SSHD_BIN" != "/usr/sbin/sshd" ]]; then
  block "live-sshd-bin-not-fixed"
fi

verify_live_bootstrap_path() {
  [[ -z "$DESTDIR" ]] || return 0
  local running
  running="$(realpath -e -- "${BASH_SOURCE[0]}")" || block "protected-bootstrap-unavailable"
  [[ "$running" == "$PROTECTED_BOOTSTRAP_PATH" ]] || block "direct-checkout-bootstrap-forbidden"
  [[ ! -L "$PROTECTED_BOOTSTRAP_PATH" && -f "$PROTECTED_BOOTSTRAP_PATH" ]] || block "protected-bootstrap-unsafe"
  local meta uid gid mode
  meta="$("$STAT_BIN" -Lc '%u:%g:%a' -- "$PROTECTED_BOOTSTRAP_PATH")" || block "protected-bootstrap-stat-failed"
  IFS=: read -r uid gid mode <<<"$meta"
  [[ "$uid" == "0" && "$gid" == "0" ]] || block "protected-bootstrap-owner-mismatch"
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] || block "protected-bootstrap-mode-invalid"
  (( (8#$mode & 8#022) == 0 )) || block "protected-bootstrap-writable"
}

verify_live_bootstrap_path

CANONICAL_REPO_ROOT="$(realpath -e -- "$REPO_ROOT")" || block "repo-root-unavailable"
if [[ -z "$DESTDIR" && "$CANONICAL_REPO_ROOT" != "$CANONICAL_LIVE_REPO_ROOT" ]]; then
  block "repo-root-not-canonical-runner-controller-checkout"
fi

git_capture() {
  local cwd="$1"
  shift
  local tmp rc size
  tmp="$(mktemp "${TMPDIR:-/tmp}/skeleton-gateway-git.XXXXXX")" || block "git-output-tempfile-unavailable"
  if [[ -z "$DESTDIR" ]]; then
    if "$RUNUSER_BIN" -u "$RUNNER_USER" -- \
      "$ENV_BIN" -i \
        HOME=/nonexistent LANG=C LC_ALL=C PATH=/usr/bin:/bin \
        GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
        "$GIT_BIN" -C "$cwd" "$@" >"$tmp" 2>&1
    then
      rc=0
    else
      rc=$?
    fi
  else
    if "$ENV_BIN" -i \
      HOME=/nonexistent LANG=C LC_ALL=C PATH=/usr/bin:/bin \
      GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
      "$GIT_BIN" -C "$cwd" "$@" >"$tmp" 2>&1
    then
      rc=0
    else
      rc=$?
    fi
  fi
  size="$("$STAT_BIN" -Lc '%s' -- "$tmp")" || {
    rm -f -- "$tmp"
    block "git-output-stat-failed"
  }
  if (( size > MAX_GIT_OUTPUT_BYTES )); then
    rm -f -- "$tmp"
    block "git-output-too-large"
  fi
  cat -- "$tmp"
  rm -f -- "$tmp"
  return "$rc"
}

run_git() {
  git_capture "$CANONICAL_REPO_ROOT" "$@"
}

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

current_head="$(run_git rev-parse --verify 'HEAD^{commit}')" || block "head-unavailable"
origin_main="$(run_git rev-parse --verify 'origin/main^{commit}')" || block "origin-main-unavailable"
[[ "$current_head" == "$EXPECTED_MAIN_SHA" ]] || block "head-sha-mismatch"
[[ "$origin_main" == "$EXPECTED_MAIN_SHA" ]] || block "origin-main-sha-mismatch"
[[ -z "$(run_git status --porcelain=v1 --untracked-files=all)" ]] || block "worktree-not-clean"

if [[ -z "$DESTDIR" ]]; then
  fresh_remote_row="$(git_capture / ls-remote --exit-code "$CANONICAL_ORIGIN_HTTPS" refs/heads/main)" || block "fresh-remote-main-unavailable"
else
  fresh_remote_row="$(run_git ls-remote --exit-code origin refs/heads/main)" || block "fresh-remote-main-unavailable"
fi
[[ "$fresh_remote_row" != *$'\n'* ]] || block "fresh-remote-main-malformed"
[[ "$fresh_remote_row" == *$'\t'* ]] || block "fresh-remote-main-malformed"
fresh_remote_sha="${fresh_remote_row%%$'\t'*}"
fresh_remote_ref="${fresh_remote_row#*$'\t'}"
[[ "$fresh_remote_sha" =~ ^[0-9a-f]{40}$ && "$fresh_remote_ref" == "refs/heads/main" ]] || block "fresh-remote-main-malformed"
[[ "$fresh_remote_sha" == "$EXPECTED_MAIN_SHA" ]] || block "fresh-remote-main-mismatch"

verify_tree_entry() {
  local mode="$1"
  local path="$2"
  local line actual_mode actual_type actual_blob actual_path
  line="$(run_git ls-tree "$EXPECTED_MAIN_SHA" -- "$path")" || block "tree-entry-unavailable-$path"
  [[ -n "$line" && "$line" != *$'\n'* ]] || block "tree-entry-missing-or-malformed-$path"
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
  local expected_blob actual_blob script_path
  expected_blob="$(verify_tree_entry 100755 "$BOOTSTRAP_SOURCE_PATH")"
  if [[ -z "$DESTDIR" ]]; then
    script_path="$PROTECTED_BOOTSTRAP_PATH"
  else
    script_path="$(realpath -e -- "${BASH_SOURCE[0]}")" || block "installer-source-unavailable"
  fi
  actual_blob="$(run_git hash-object --no-filters --stdin < "$script_path")" || block "installer-hash-unavailable"
  [[ "$actual_blob" == "$expected_blob" ]] || block "installer-blob-mismatch"
}

copy_git_file() {
  local tree_mode="$1"
  local source_path="$2"
  local dest_path="$3"
  local blob tmp
  blob="$(verify_tree_entry "$tree_mode" "$source_path")"
  tmp="$(mktemp "$(dirname -- "$dest_path")/.git-object.XXXXXX")" || block "tempfile-unavailable"
  if ! run_git cat-file -p "$blob" >"$tmp"; then
    rm -f -- "$tmp"
    block "git-object-read-failed-$source_path"
  fi
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
  cat >"$dest_path"
  chmod "$mode" "$dest_path"
}

validate_sshd_fragment() {
  local candidate="$1"
  [[ -x "$SSHD_BIN" ]] || block "sshd-binary-unavailable"
  "$SSHD_BIN" -t -f "$candidate" >/dev/null 2>&1 || return 1
  return 0
}

write_sshd_fragment_candidate() {
  local candidate="$1"
  cat >"$candidate" <<EOF
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
}

validate_ssh_public_key() {
  [[ -n "$SSH_PUBLIC_KEY" ]] || return 0
  (( ${#SSH_PUBLIC_KEY} <= MAX_SSH_KEY_BYTES )) || block "ssh-public-key-too-large"
  [[ "$SSH_PUBLIC_KEY" != *$'\n'* && "$SSH_PUBLIC_KEY" != *$'\r'* ]] || block "ssh-public-key-multiline"
  local algorithm key_blob comment extra
  IFS=' ' read -r algorithm key_blob comment extra <<<"$SSH_PUBLIC_KEY"
  [[ -n "$algorithm" && -n "$key_blob" && -z "${extra:-}" ]] || block "ssh-public-key-format-invalid"
  [[ "$algorithm" == "ssh-ed25519" || "$algorithm" == "ecdsa-sha2-nistp256" ]] || block "unapproved-ssh-public-key-type"
  [[ "$key_blob" =~ ^[A-Za-z0-9+/]+={0,2}$ ]] || block "ssh-public-key-base64-invalid"
  if [[ -n "${comment:-}" ]]; then
    [[ "$comment" =~ ^[A-Za-z0-9._@:+-]{1,128}$ ]] || block "ssh-public-key-comment-invalid"
  fi
}

validate_ssh_inputs_before_install() {
  validate_ssh_public_key
  [[ -n "$SSH_PUBLIC_KEY" ]] || return 0
  if [[ -z "$DESTDIR" ]]; then
    [[ -x "$SSH_SHELL" ]] || block "ssh-shell-unavailable"
  fi
  local candidate
  candidate="$(mktemp "${TMPDIR:-/tmp}/skeleton-runner-controller-sshd-fragment.XXXXXX")" || block "sshd-candidate-unavailable"
  write_sshd_fragment_candidate "$candidate"
  if ! validate_sshd_fragment "$candidate"; then
    rm -f -- "$candidate"
    block "sshd-validation-failed"
  fi
  rm -f -- "$candidate"
}

install_sudoers_rule() {
  local absolute_path="$1"
  local content="$2"
  local dest candidate
  dest="$(target "$absolute_path")"
  candidate="$(mktemp "${TMPDIR:-/tmp}/skeleton-runner-controller-sudoers.XXXXXX")" || block "sudoers-candidate-unavailable"
  printf '%s\n' "$content" >"$candidate"
  chmod 0440 "$candidate"
  "$VISUDO_BIN" -cf "$candidate" >/dev/null 2>&1 || {
    rm -f -- "$candidate"
    block "sudoers-validation-failed"
  }
  install -d -m 0755 "$(dirname -- "$dest")"
  install -o root -g root -m 0440 "$candidate" "$dest" 2>/dev/null || install -m 0440 "$candidate" "$dest"
  rm -f -- "$candidate"
}

install_ssh_account_live() {
  [[ -x "$SSH_SHELL" ]] || block "ssh-shell-unavailable"
  if ! getent passwd "$SSH_USER" >/dev/null; then
    useradd --system --home-dir /nonexistent --shell "$SSH_SHELL" --no-create-home "$SSH_USER"
  fi
  passwd -l "$SSH_USER" >/dev/null 2>&1 || block "ssh-account-lock-failed"
  usermod --home /nonexistent --shell "$SSH_SHELL" "$SSH_USER"
}

verify_effective_sshd_live() {
  local effective
  effective="$("$SSHD_BIN" -T -C user="$SSH_USER",host=localhost,addr=127.0.0.1 2>/dev/null)" || block "sshd-effective-config-unavailable"
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
validate_ssh_inputs_before_install

install -d -m 0755 "$(target "$INSTALL_ROOT/core")" "$(target "$INSTALL_ROOT/core/home_edge")" "$(target "$EXEC_ROOT")"
install -d -m 0700 "$(target "$STATE_ROOT")"
printf '%s\n' '# installed runner-controller gateway package' >"$(target "$INSTALL_ROOT/core/__init__.py")"
chmod 0444 "$(target "$INSTALL_ROOT/core/__init__.py")"
printf '%s\n' '# installed runner-controller gateway home-edge package' >"$(target "$INSTALL_ROOT/core/home_edge/__init__.py")"
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

install_sudoers_rule "$SUDOERS_PATH" "$RUNNER_USER ALL=(root) NOPASSWD: $EXEC_ROOT/privileged-gateway \"\""

if [[ -z "$SSH_PUBLIC_KEY" ]]; then
  printf 'DONE: Runner controller privileged gateway files installed inertly\n'
  printf 'gateway=%s\n' "$EXEC_ROOT/privileged-gateway"
  printf 'sudoers=%s\n' "$SUDOERS_PATH"
  printf 'ssh=DISABLED_NOT_CONFIGURED\n'
  exit 0
fi

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

install_sudoers_rule "$SSH_SUDOERS_PATH" "$SSH_USER ALL=(root) NOPASSWD: $EXEC_ROOT/privileged-gateway \"\""

install -d -m 0755 "$(dirname -- "$(target "$SSHD_FRAGMENT")")"
candidate="$(mktemp "$(target "$SSHD_FRAGMENT").candidate.XXXXXX")" || block "sshd-candidate-unavailable"
write_sshd_fragment_candidate "$candidate"
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
  verify_effective_sshd_live
fi

printf 'DONE: Runner controller privileged gateway files installed inertly\n'
printf 'gateway=%s\n' "$EXEC_ROOT/privileged-gateway"
printf 'sudoers=%s\n' "$SUDOERS_PATH"
printf 'ssh=READY\n'
printf 'ssh_user=%s\n' "$SSH_USER"
printf 'sshd_fragment=%s\n' "$SSHD_FRAGMENT"
