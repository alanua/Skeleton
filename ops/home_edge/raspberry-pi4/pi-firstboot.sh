#!/usr/bin/env bash
set -euo pipefail
exec > >(tee -a /var/log/skeleton-pi-firstboot.log) 2>&1
config=/boot/firmware/skeleton-node.env
keys=/boot/firmware/authorized_keys
[[ -r "$config" && -s "$keys" ]] || { echo "waiting for boot provisioning files"; exit 75; }
set -a; . "$config"; set +a
node_id="${SKELETON_NODE_ID:-kyiv-media-pi4-01}"
admin="${SKELETON_ADMIN_USER:-skeleton}"
[[ "$node_id" =~ ^[A-Za-z0-9_.-]+$ && "$admin" =~ ^[a-z_][a-z0-9_-]*$ ]] || exit 2
hostnamectl set-hostname "$node_id"
export DEBIAN_FRONTEND=noninteractive
for _ in $(seq 1 60); do getent hosts deb.debian.org >/dev/null 2>&1 && break; sleep 2; done
apt-get update
packages=(ca-certificates curl jq openssh-server python3 python3-venv python3-yaml rsync sqlite3 zstd acl mpv pipewire pipewire-pulse wireplumber alsa-utils cec-utils sway swayidle xwayland fonts-noto-core fonts-noto-color-emoji)
[[ ${SKELETON_INSTALL_BROWSER:-0} == 1 ]] && packages+=(chromium)
apt-get install -y --no-install-recommends "${packages[@]}"
if ! id "$admin" >/dev/null 2>&1; then useradd -m -s /bin/bash "$admin"; fi
for group in sudo video render input audio; do getent group "$group" >/dev/null && usermod -aG "$group" "$admin"; done
install -d -m 0700 -o "$admin" -g "$admin" "/home/$admin/.ssh"
install -m 0600 -o "$admin" -g "$admin" "$keys" "/home/$admin/.ssh/authorized_keys"
passwd -l "$admin" || true
/opt/skeleton-image/resolver_federation/install.sh
sed -i "s/^SKELETON_RESOLVER_NODE_ID=.*/SKELETON_RESOLVER_NODE_ID=$node_id/" /etc/skeleton/resolver-sync.env
install -d -m 0750 /var/lib/skeleton/node-bootstrap
cat >/var/lib/skeleton/node-bootstrap/status.json <<EOF
{"schema":"skeleton.node_bootstrap.v1","node_id":"$node_id","profile":"display_direct_play","stage":"base_ready_media_enrollment_pending","resolver_federation":"installed","secrets_present":false}
EOF
systemctl enable --now ssh skeleton-resolver-sync.timer skeleton-resolver-inbox.path
systemctl disable skeleton-pi-firstboot.service
rm -f /etc/systemd/system/multi-user.target.wants/skeleton-pi-firstboot.service
