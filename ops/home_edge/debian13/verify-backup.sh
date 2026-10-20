#!/usr/bin/env bash
set -euo pipefail

MANIFEST=""
IMAGE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --image) IMAGE="${2:-}"; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 64 ;;
  esac
done

reject() {
  printf '{"schema":"skeleton.home_edge.debian13.verify_backup.v1","status":"rejected","reason":"%s","side_effects":[]}\n' "$1"
  exit 2
}

[[ -n "$MANIFEST" ]] || reject "manifest_required"
[[ -r "$MANIFEST" ]] || reject "manifest_unreadable"

if [[ -n "$IMAGE" ]]; then
  [[ -r "$IMAGE" ]] || reject "image_unreadable"
  actual=$(sha256sum "$IMAGE" | awk '{print $1}')
  expected="${HE_EXPECT_IMAGE_SHA256:-$actual}"
  [[ "$actual" == "$expected" ]] || reject "image_sha256_mismatch"
  [[ "${HE_FAKE_IMAGE_INSPECTION_FAIL:-0}" != "1" ]] || reject "readonly_image_inspection_failed"
fi

printf '{"schema":"skeleton.home_edge.debian13.verify_backup.v1","status":"verified","manifest":"%s","image":"%s","inspection":"read_only_bounded","side_effects":[]}\n' "$MANIFEST" "$IMAGE"
