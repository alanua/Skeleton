#!/usr/bin/env bash
set -euo pipefail
unit_src="$(cd "$(dirname "$0")/.." && pwd)/ops/systemd/skeleton-family-document-intake.service"
unit_dst="${HOME}/.config/systemd/user/skeleton-family-document-intake.service"
install -d -m 0700 "$(dirname "$unit_dst")"
install -m 0600 "$unit_src" "$unit_dst"
systemctl --user daemon-reload
printf '%s\n' 'installed_not_enabled_or_started'
