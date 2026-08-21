#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${SKELETON_MAIL_INSTALL_ROOT:-/opt/skeleton-mail-operations}"
STATE_ROOT="${SKELETON_MAIL_STATE_ROOT:-/var/lib/skeleton/mail}"
SERVICE_USER="${SKELETON_MAIL_USER:-agent}"
SERVICE_GROUP="${SKELETON_MAIL_GROUP:-agent}"

install -d -m 0755 "$INSTALL_ROOT"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$STATE_ROOT"
install -m 0755 scripts/mail_operations_worker.py "$INSTALL_ROOT/mail_operations_worker.py"
install -m 0644 ops/systemd/skeleton-mail-operations.service /etc/systemd/system/skeleton-mail-operations.service
install -m 0644 ops/systemd/skeleton-mail-operations.timer /etc/systemd/system/skeleton-mail-operations.timer

systemctl daemon-reload
systemctl disable skeleton-mail-operations.timer >/dev/null 2>&1 || true
