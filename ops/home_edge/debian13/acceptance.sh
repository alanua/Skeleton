#!/usr/bin/env bash
set -euo pipefail

software_status() {
  local id="$1"
  if [[ "${HE_ACCEPTANCE_ALL_SOFTWARE_PASS:-0}" == "1" ]]; then
    printf 'passed'
  else
    case "$id" in
      pipewire_creative|samsung_kiosk|youtube_vaapi) printf 'failed' ;;
      *) printf 'pending' ;;
    esac
  fi
}

item() {
  local id="$1" physical="$2"
  local status
  status=$(software_status "$id")
  printf '{"id":"%s","type":"software","sent":true,"accepted":%s,"applied":%s,"physically_verified":false,"physical_required":%s,"status":"%s"}' \
    "$id" \
    "$([[ "$status" == "passed" ]] && printf true || printf false)" \
    "$([[ "$status" == "passed" ]] && printf true || printf false)" \
    "$physical" \
    "$([[ "$physical" == "true" && "$status" == "passed" ]] && printf physical_pending || printf "$status")"
}

items=(
  debian13_identity:false network_stack:false executor_ops:false pipewire_creative:true
  youtube_vaapi:true mpv_iptv:true games_mode:true hyperhdr_wled:true
  brother_adf:true samsung_kiosk:true skeleton_cast:false watchdogs:false
  registry_sqlite_memorygate:false service_persistence:false
)

failed=0
printf '{"schema":"skeleton.home_edge.debian13.acceptance.v1","node":"home-edge-01","generated_at":"%s","items":[' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
first=1
for entry in "${items[@]}"; do
  id=${entry%%:*}
  physical=${entry##*:}
  [[ $first -eq 1 ]] || printf ','
  first=0
  item "$id" "$physical"
  [[ "$(software_status "$id")" == "passed" ]] || failed=1
done
printf '],"invariants":{"pipewire_sink":"alsa_output.pci-0000_00_1f.3.analog-stereo","pipewire_device":"HDA Intel PCH","pipewire_codec":"ALC671 Analog","hdmi_default_allowed":false,"samsung_contract":"external-tablet-kiosk-current","obsolete_local_display_workload":false,"obsolete_local_display_port":false,"youtube_vaapi_requires_live_decode":true},"overall":"%s"}\n' "$([[ $failed -eq 0 ]] && printf software_passed_physical_pending || printf failed)"
exit "$failed"
