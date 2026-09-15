#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
  echo "installer_requires_root" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
unit_source="${repo_root}/ops/systemd/skeleton-family-document-intake.service"
unit_target="/etc/systemd/system/skeleton-family-document-intake.service"

if [ ! -f "${unit_source}" ]; then
  echo "family_document_unit_missing" >&2
  exit 3
fi
if grep -Fq '/home/agent/agent-dev/Skeleton' "${unit_source}"; then
  echo "runner_worktree_path_forbidden" >&2
  exit 4
fi

install -D -o root -g root -m 0644 "${unit_source}" "${unit_target}"
systemctl daemon-reload
systemd-analyze verify "${unit_target}" >/dev/null
