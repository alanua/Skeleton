#!/usr/bin/env bash
set -euo pipefail
apply=0; if [[ ${1:-} == --apply ]]; then apply=1; shift; fi
[[ $# -eq 1 && $(id -u) -eq 0 ]] || { echo "Usage: sudo $0 [--apply] BACKUP_DIR" >&2; exit 2; }
backup="$(realpath -e "$1")"; [[ -f "$backup/SHA256SUMS" && -d "$backup/tree" ]] || exit 2
(cd "$backup"; sha256sum -c SHA256SUMS)
[[ $apply -eq 1 ]] || { echo 'Backup verified; plan only'; exit 0; }
rsync -aHAX --numeric-ids "$backup/tree/" /
chown -R 1000:1000 /home/valertos08/.config /home/valertos08/.local 2>/dev/null || true
chmod 0700 /etc/skeleton 2>/dev/null || true; find /etc/skeleton -type f -exec chmod 0600 {} + 2>/dev/null || true
systemctl daemon-reload; sudo -u valertos08 XDG_RUNTIME_DIR=/run/user/1000 systemctl --user daemon-reload || true; sync
