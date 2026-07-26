#!/usr/bin/env bash
set -euo pipefail

install_root="${1:-$HOME/.local/bin}"
mkdir -p "$install_root"
cp "$(dirname "$0")/family_document_worker.py" "$install_root/skeleton-family-document-worker"
chmod 0755 "$install_root/skeleton-family-document-worker"
printf 'installed=%s\n' "$install_root/skeleton-family-document-worker"
