#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${SKELETON_MAIL_INSTALL_ROOT:-/opt/skeleton-mail-operations}"
PRODUCTION_HELPER_PATH="/opt/skeleton-mail-operations/bitwarden_gmail_reference_bootstrap.py"
PRODUCTION_RUNTIME_PYTHON="/opt/skeleton-mail-operations/bitwarden-sdk-runtime/bin/python"
STATE_ROOT="${SKELETON_MAIL_STATE_ROOT:-/var/lib/skeleton/mail}"
SERVICE_USER="${SKELETON_MAIL_USER:-agent}"
SERVICE_GROUP="${SKELETON_MAIL_GROUP:-agent}"
RUNTIME_PYTHON="$INSTALL_ROOT/bitwarden-sdk-runtime/bin/python"

if [[ "$INSTALL_ROOT" == "/opt/skeleton-mail-operations" ]]; then
  [[ "$RUNTIME_PYTHON" == "$PRODUCTION_RUNTIME_PYTHON" ]]
  [[ "$INSTALL_ROOT/bitwarden_gmail_reference_bootstrap.py" == "$PRODUCTION_HELPER_PATH" ]]
fi

install -d -m 0755 "$INSTALL_ROOT"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$STATE_ROOT"
scripts/install_bitwarden_sdk_runtime.sh
install -m 0755 scripts/mail_operations_worker.py "$INSTALL_ROOT/mail_operations_worker.py.tmp"
install -m 0755 scripts/bitwarden_gmail_reference_bootstrap.py "$INSTALL_ROOT/bitwarden_gmail_reference_bootstrap.py.tmp"
mv "$INSTALL_ROOT/mail_operations_worker.py.tmp" "$INSTALL_ROOT/mail_operations_worker.py"
mv "$INSTALL_ROOT/bitwarden_gmail_reference_bootstrap.py.tmp" "$INSTALL_ROOT/bitwarden_gmail_reference_bootstrap.py"
"$RUNTIME_PYTHON" - <<PY
import importlib.util
from pathlib import Path
path = Path("$INSTALL_ROOT/bitwarden_gmail_reference_bootstrap.py")
spec = importlib.util.spec_from_file_location("bitwarden_gmail_reference_bootstrap", path)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
assert callable(module.main)
assert module.SDK_PACKAGE_VERSION == "2.1.0"
PY
install -m 0644 ops/systemd/skeleton-mail-operations.service /etc/systemd/system/skeleton-mail-operations.service
install -m 0644 ops/systemd/skeleton-mail-operations.timer /etc/systemd/system/skeleton-mail-operations.timer

systemctl daemon-reload
systemctl disable skeleton-mail-operations.timer >/dev/null 2>&1 || true
