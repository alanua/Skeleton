#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "install_scheduler_core.sh must run as root" >&2
  exit 2
fi

repo_root="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
install_root="${SKELETON_SCHEDULER_INSTALL_ROOT:-/opt/skeleton-scheduler}"
state_root="${SKELETON_SCHEDULER_STATE_ROOT:-/var/lib/skeleton/scheduler}"
service_user="${SKELETON_SCHEDULER_USER:-agent}"
service_group="${SKELETON_SCHEDULER_GROUP:-${service_user}}"

getent passwd "${service_user}" >/dev/null
getent group "${service_group}" >/dev/null

install -d -m 0755 "${install_root}/core" "${install_root}/scripts"
install -d -m 0700 -o "${service_user}" -g "${service_group}" "${state_root}"
install -m 0644 "${repo_root}/core/__init__.py" "${install_root}/core/__init__.py"
install -m 0644 "${repo_root}/core/scheduler_models.py" "${install_root}/core/scheduler_models.py"
install -m 0644 "${repo_root}/core/scheduler_store.py" "${install_root}/core/scheduler_store.py"
install -m 0644 "${repo_root}/core/scheduler_engine.py" "${install_root}/core/scheduler_engine.py"
install -m 0755 "${repo_root}/scripts/scheduler_tick.py" "${install_root}/scripts/scheduler_tick.py"

render_unit() {
  local source="$1"
  local target="$2"
  sed \
    -e "s|@INSTALL_ROOT@|${install_root}|g" \
    -e "s|@STATE_ROOT@|${state_root}|g" \
    -e "s|@SERVICE_USER@|${service_user}|g" \
    -e "s|@SERVICE_GROUP@|${service_group}|g" \
    "${source}" > "${target}.tmp"
  chmod 0644 "${target}.tmp"
  mv -f "${target}.tmp" "${target}"
}

render_unit \
  "${repo_root}/ops/systemd/skeleton-scheduler.service" \
  "/etc/systemd/system/skeleton-scheduler.service"
render_unit \
  "${repo_root}/ops/systemd/skeleton-scheduler.timer" \
  "/etc/systemd/system/skeleton-scheduler.timer"

systemctl daemon-reload
systemctl enable --now skeleton-scheduler.timer
systemctl start skeleton-scheduler.service

service_state="$(systemctl is-active skeleton-scheduler.service || true)"
timer_state="$(systemctl is-active skeleton-scheduler.timer || true)"
timer_enabled="$(systemctl is-enabled skeleton-scheduler.timer || true)"
if [[ "${service_state}" != "inactive" && "${service_state}" != "active" ]]; then
  echo "scheduler service failed: ${service_state}" >&2
  exit 3
fi
if [[ "${timer_state}" != "active" || "${timer_enabled}" != "enabled" ]]; then
  echo "scheduler timer activation failed" >&2
  exit 4
fi

/usr/bin/python3 "${install_root}/scripts/scheduler_tick.py" \
  --db "${state_root}/scheduler.sqlite3" status
