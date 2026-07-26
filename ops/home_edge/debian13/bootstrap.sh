#!/usr/bin/env bash
set -euo pipefail
apply=0; [[ ${1:-} == --apply ]] && apply=1
[[ $# -le 1 && $(id -u) -eq 0 ]] || { echo "Usage: sudo $0 [--apply]" >&2; exit 2; }
. /etc/os-release
[[ ${ID:-} == debian && ${VERSION_ID%%.*} == 13 && $(dpkg --print-architecture) == amd64 ]] || { echo 'requires Debian 13 amd64' >&2; exit 2; }
user=valertos08; [[ $(id -u "$user" 2>/dev/null) == 1000 ]] || { echo 'valertos08 UID 1000 required' >&2; exit 2; }
packages=(sudo openssh-server git curl ca-certificates gnupg jq rsync sqlite3 acl dbus-user-session python3 python3-venv python3-pip python3-requests python3-yaml greetd sway swayidle swaylock xwayland foot chromium mpv pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth alsa-utils vainfo intel-media-va-driver i965-va-driver cups cups-client printer-driver-all sane-airscan sane-utils ipp-usb tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng tesseract-ocr-ukr ocrmypdf ghostscript poppler-utils imagemagick pciutils usbutils ethtool evtest joystick)
printf 'Install: %s
No VLC, Waydroid or GNOME.
' "${packages[*]}"
[[ $apply -eq 1 ]] || { echo 'Plan only'; exit 0; }
export DEBIAN_FRONTEND=noninteractive
apt-get update; apt-get install -y --no-install-recommends "${packages[@]}"
hostnamectl set-hostname home-edge-01
for g in video render input audio lp lpadmin scanner; do getent group "$g" >/dev/null && usermod -aG "$g" "$user"; done
install -d -m 0700 -o "$user" -g "$user" /home/$user/.config/sway /home/$user/.config/systemd/user /home/$user/.local/state/skeleton
install -d -m 0755 /etc/skeleton /var/lib/skeleton /var/log/skeleton; chmod 0700 /etc/skeleton
loginctl enable-linger "$user"; systemctl enable --now ssh cups; systemctl enable greetd
sudo -u "$user" XDG_RUNTIME_DIR=/run/user/1000 systemctl --user enable pipewire.socket pipewire-pulse.socket wireplumber.service || true
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [[ -x "$repo_root/scripts/install_home_edge_executor.sh" && -f /etc/skeleton/home_edge_executor.env ]]; then "$repo_root/scripts/install_home_edge_executor.sh" --desktop-user "$user" --ssh-target-user "$user"; else echo 'executor install deferred until private env restore'; fi
