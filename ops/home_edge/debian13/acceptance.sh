#!/usr/bin/env bash
set -u
fail=0; pass(){ echo "PASS  $*"; }; warn(){ echo "WARN  $*"; }; check(){ n="$1"; shift; if "$@" >/dev/null 2>&1; then pass "$n"; else echo "FAIL  $n"; fail=1; fi; }
. /etc/os-release
[[ ${ID:-} == debian && ${VERSION_ID%%.*} == 13 ]] && pass 'Debian 13' || fail=1
[[ $(id -u valertos08 2>/dev/null) == 1000 ]] && pass 'UID 1000' || fail=1
check 'SSH' systemctl is-active --quiet ssh; check 'CUPS' systemctl is-active --quiet cups; check 'i915' grep -q '^i915 ' /proc/modules
check 'MPV' command -v mpv; check 'Chromium' command -v chromium; check 'Sway' command -v sway; check 'PipeWire' command -v pipewire; check 'VAAPI' vainfo
[[ -x /usr/local/bin/home_edge_exec ]] && pass 'Home Edge executor' || { echo 'FAIL  Home Edge executor'; fail=1; }
[[ -f /home/valertos08/.config/skeleton/memory-gate/embodied-system.json ]] && pass 'MemoryGate' || fail=1
[[ -f /home/valertos08/.config/skeleton/device-registry/confirmed.yaml ]] && pass 'registry' || fail=1
[[ ! -x /usr/bin/vlc ]] && pass 'VLC absent' || warn 'VLC present'; [[ ! -d /var/lib/waydroid ]] && pass 'Waydroid absent' || warn 'Waydroid data present'
echo 'Manual: HDMI, audio, HyperHDR/WLED, MFP, Home remote latency, and automatic gamepad in games mode.'; exit "$fail"
