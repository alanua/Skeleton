#!/usr/bin/env bash
set -euo pipefail
exec > >(tee -a /var/log/skeleton-image-firstboot.log) 2>&1
export DEBIAN_FRONTEND=noninteractive
for _ in $(seq 1 60); do getent hosts deb.debian.org >/dev/null 2>&1 && break; sleep 2; done
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git jq openssh-server python3 python3-venv python3-yaml qemu-guest-agent rsync sqlite3 zstd acl
install -d -m 0700 /etc/skeleton
node_id="$(hostname -s)"
if [[ -r /etc/skeleton/node.env ]]; then
  set -a; . /etc/skeleton/node.env; set +a
  node_id="${SKELETON_NODE_ID:-$node_id}"
fi
[[ "$node_id" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "invalid node id" >&2; exit 2; }
hostnamectl set-hostname "$node_id"
/opt/skeleton-image/resolver_federation/install.sh
sed -i "s/^SKELETON_RESOLVER_NODE_ID=.*/SKELETON_RESOLVER_NODE_ID=$node_id/" /etc/skeleton/resolver-sync.env
systemctl enable --now ssh qemu-guest-agent skeleton-resolver-sync.timer skeleton-resolver-inbox.path
install -d -m 0750 /var/lib/skeleton/node-bootstrap
cat >/var/lib/skeleton/node-bootstrap/status.json <<EOF
{"schema":"skeleton.node_bootstrap.v1","node_id":"$node_id","stage":"base_ready_enrollment_pending","resolver_federation":"installed","secrets_present":false}
EOF
systemctl disable skeleton-image-firstboot.service
rm -f /etc/systemd/system/multi-user.target.wants/skeleton-image-firstboot.service
