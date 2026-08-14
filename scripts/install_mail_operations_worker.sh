#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "install_mail_operations_worker.sh must run as root" >&2
  exit 2
fi

repo_root="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
install_root="${SKELETON_MAIL_INSTALL_ROOT:-/opt/skeleton-mail-operations}"
state_root="${SKELETON_MAIL_STATE_ROOT:-/var/lib/skeleton/mail-operations}"
scheduler_state_root="${SKELETON_SCHEDULER_STATE_ROOT:-/var/lib/skeleton/scheduler}"
service_user="${SKELETON_MAIL_USER:-agent}"
service_group="${SKELETON_MAIL_GROUP:-${service_user}}"

getent passwd "${service_user}" >/dev/null
getent group "${service_group}" >/dev/null

install -d -m 0755 "${install_root}/core" "${install_root}/adapters" "${install_root}/integrations" "${install_root}/scripts"
install -d -m 0700 -o "${service_user}" -g "${service_group}" "${state_root}"
install -m 0644 "${repo_root}/core/__init__.py" "${install_root}/core/__init__.py"
install -m 0644 "${repo_root}/core/domain_event_graph.py" "${install_root}/core/domain_event_graph.py"
install -m 0644 "${repo_root}/core/mail_operations.py" "${install_root}/core/mail_operations.py"
install -m 0644 "${repo_root}/core/mail_provider.py" "${install_root}/core/mail_provider.py"
install -m 0644 "${repo_root}/core/mail_runtime.py" "${install_root}/core/mail_runtime.py"
install -m 0644 "${repo_root}/core/mail_state.py" "${install_root}/core/mail_state.py"
install -m 0644 "${repo_root}/core/scheduler_models.py" "${install_root}/core/scheduler_models.py"
install -m 0644 "${repo_root}/core/scheduler_store.py" "${install_root}/core/scheduler_store.py"
install -m 0644 "${repo_root}/core/shared_dispatch.py" "${install_root}/core/shared_dispatch.py"
install -m 0644 "${repo_root}/adapters/gmail_mail_provider.py" "${install_root}/adapters/gmail_mail_provider.py"
install -m 0644 "${repo_root}/integrations/mail_scheduler.py" "${install_root}/integrations/mail_scheduler.py"
install -m 0644 "${repo_root}/integrations/mail_telegram.py" "${install_root}/integrations/mail_telegram.py"
install -m 0755 "${repo_root}/scripts/mail_operations_worker.py" "${install_root}/scripts/mail_operations_worker.py"

render_unit() {
  local source="$1"
  local target="$2"
  sed \
    -e "s|@INSTALL_ROOT@|${install_root}|g" \
    -e "s|@STATE_ROOT@|${state_root}|g" \
    -e "s|@SCHEDULER_STATE_ROOT@|${scheduler_state_root}|g" \
    -e "s|@SERVICE_USER@|${service_user}|g" \
    -e "s|@SERVICE_GROUP@|${service_group}|g" \
    "${source}" > "${target}.tmp"
  chmod 0644 "${target}.tmp"
  mv -f "${target}.tmp" "${target}"
}

render_unit "${repo_root}/ops/systemd/skeleton-mail-operations.service" "/etc/systemd/system/skeleton-mail-operations.service"
render_unit "${repo_root}/ops/systemd/skeleton-mail-operations.timer" "/etc/systemd/system/skeleton-mail-operations.timer"

systemctl daemon-reload
/usr/bin/python3 "${install_root}/scripts/mail_operations_worker.py" \
  --config "${state_root}/accounts.json" \
  --state-db "${state_root}/mail.sqlite3" \
  --scheduler-db "${scheduler_state_root}/scheduler.sqlite3" \
  --health
