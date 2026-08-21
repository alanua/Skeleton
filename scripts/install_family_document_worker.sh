#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "must run as root" >&2
  exit 1
fi

install -d -m 0755 /etc/systemd/system
install -d -m 0700 /etc/skeleton
install -d -m 0700 /var/lib/skeleton/family-document/inbox
install -d -m 0700 /var/lib/skeleton/family-document/archive
install -d -m 0700 /var/lib/skeleton/family-document/outbox
install -d -m 0700 /var/lib/skeleton/private-memory
install -d -m 0755 /var/log/skeleton /run/skeleton

if [[ ! -f /etc/skeleton-family-document-intake.env ]]; then
  umask 077
  cat > /etc/skeleton-family-document-intake.env <<'ENV'
SKELETON_FAMILY_DOCUMENT_INBOX=/var/lib/skeleton/family-document/inbox
SKELETON_FAMILY_DOCUMENT_ARCHIVE=/var/lib/skeleton/family-document/archive
SKELETON_FAMILY_DOCUMENT_OUTBOX_DB=/var/lib/skeleton/family-document/outbox/receipts.sqlite3
SKELETON_SCHEDULER_DB=/var/lib/skeleton/scheduler/scheduler.sqlite3
SKELETON_PRIVATE_MEMORY_ROOT=/var/lib/skeleton/private-memory
ENV
fi

install -D -m 0644 ops/systemd/skeleton-family-document-intake.service \
  /etc/systemd/system/skeleton-family-document-intake.service
systemctl daemon-reload
echo "Installed skeleton-family-document-intake.service without enabling or starting it."
