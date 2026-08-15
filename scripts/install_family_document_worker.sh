#!/usr/bin/env bash
set -euo pipefail

install -D -m 0644 ops/systemd/skeleton-family-document-intake.service \
  /etc/systemd/system/skeleton-family-document-intake.service
systemctl daemon-reload
systemctl enable skeleton-family-document-intake.service
