#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 2; }
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
user=skeleton-resolver
if ! id "$user" >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/skeleton-resolver --create-home --shell /bin/bash "$user"
fi
usermod --shell /bin/bash "$user"
passwd -l "$user" >/dev/null 2>&1 || true
install -d -m 0700 -o "$user" -g "$user" /var/lib/skeleton-resolver/.ssh
install -d -m 0750 -o "$user" -g "$user" /var/lib/skeleton-resolver/{inbox,outbox,archive,failed}
install -d -m 0700 -o root -g root /etc/skeleton/resolver-sync
install -d -m 0755 /usr/local/lib/skeleton-resolver
install -m 0755 "$root/resolver_sync.py" /usr/local/lib/skeleton-resolver/resolver_sync.py
install -m 0755 "$root/resolver_receive.py" /usr/local/lib/skeleton-resolver/resolver_receive.py
install -m 0755 "$root/resolver_receive_from_ssh.sh" /usr/local/lib/skeleton-resolver/resolver_receive_from_ssh.sh
cat >/usr/local/bin/skeleton-resolver-sync <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ -r /etc/skeleton/resolver-sync.env ]] && { set -a; . /etc/skeleton/resolver-sync.env; set +a; }
exec /usr/bin/python3 /usr/local/lib/skeleton-resolver/resolver_sync.py "$@"
EOF
cat >/usr/local/bin/skeleton-resolver-receive <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 /usr/local/lib/skeleton-resolver/resolver_receive.py "$@"
EOF
cat >/usr/local/bin/skeleton-resolver-receive-from-ssh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/local/lib/skeleton-resolver/resolver_receive_from_ssh.sh
EOF
chmod 0755 /usr/local/bin/skeleton-resolver-*
if [[ ! -e /etc/skeleton/resolver-sync.env ]]; then
  cat >/etc/skeleton/resolver-sync.env <<'EOF'
SKELETON_RESOLVER_NODE_ID=identity-pending
SKELETON_RESOLVER_STATE=/var/lib/skeleton-resolver
SKELETON_RESOLVER_DB=/var/lib/skeleton-resolver/resolver-learning.sqlite3
SKELETON_RESOLVER_PEERS=
SKELETON_RESOLVER_SSH_USER=skeleton-resolver
SKELETON_RESOLVER_SSH_IDENTITY=/var/lib/skeleton-resolver/.ssh/id_ed25519
EOF
  chmod 0600 /etc/skeleton/resolver-sync.env
fi
install -m 0644 "$root/skeleton-resolver-sync.service" /etc/systemd/system/skeleton-resolver-sync.service
install -m 0644 "$root/skeleton-resolver-sync.timer" /etc/systemd/system/skeleton-resolver-sync.timer
install -m 0644 "$root/skeleton-resolver-inbox.path" /etc/systemd/system/skeleton-resolver-inbox.path
systemctl daemon-reload
systemctl enable skeleton-resolver-sync.timer skeleton-resolver-inbox.path
