#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SERVICE_USER="${SERVICE_USER:-agent}"

install -d -m 0755 "${SYSTEMD_DIR}"
install -m 0644 "${ROOT}/ops/systemd/skeleton-mail-operations.service" "${SYSTEMD_DIR}/skeleton-mail-operations.service"
install -m 0644 "${ROOT}/ops/systemd/skeleton-mail-operations.timer" "${SYSTEMD_DIR}/skeleton-mail-operations.timer"

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl enable skeleton-mail-operations.timer
fi

printf 'installed skeleton-mail-operations.timer for user=%s\n' "${SERVICE_USER}"
