#!/usr/bin/env bash
set -euo pipefail
umask 077

machine_id=""
node_class=""
expected_host=""
controller_pub_url=""
controller_pub_sha256=""
receipt_path="${HOME}/.local/state/skeleton/device-trust-enroll/receipt.json"
key_path="${HOME}/.config/skeleton/machine-identity/ssh_ed25519"

die() {
  printf 'RESULT=ERROR\nREASON=%s\n=== EXIT: ERROR ===\n' "$1"
  exit 2
}

while (($#)); do
  case "$1" in
    --machine-id) machine_id="${2:-}"; shift 2;;
    --node-class) node_class="${2:-}"; shift 2;;
    --expected-host) expected_host="${2:-}"; shift 2;;
    --controller-pub-url) controller_pub_url="${2:-}"; shift 2;;
    --controller-pub-sha256) controller_pub_sha256="${2:-}"; shift 2;;
    --receipt-path) receipt_path="${2:-}"; shift 2;;
    *) die "unknown_argument";;
  esac
done

[[ "$machine_id" =~ ^[a-z0-9][a-z0-9_.-]{2,63}$ ]] || die "invalid_machine_id"
[[ "$node_class" =~ ^[a-z0-9][a-z0-9_.-]{2,63}$ ]] || die "invalid_node_class"
command -v ssh-keygen >/dev/null 2>&1 || die "ssh_keygen_missing"
command -v python3 >/dev/null 2>&1 || die "python3_missing"

host="$(hostname)"
if [[ -n "$expected_host" && "$host" != "$expected_host" ]]; then
  die "hostname_mismatch"
fi

mkdir -p "$(dirname "$key_path")" "$(dirname "$receipt_path")"
chmod 700 "$(dirname "$key_path")" "$(dirname "$receipt_path")"

if [[ ! -s "$key_path" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C "skeleton-machine:${machine_id}:v1" -f "$key_path"
fi
chmod 600 "$key_path"
chmod 644 "$key_path.pub"

fingerprint="$(ssh-keygen -lf "$key_path.pub" | awk '{print $2}')"
[[ "$fingerprint" == SHA256:* ]] || die "fingerprint_invalid"

if [[ -n "$controller_pub_url" || -n "$controller_pub_sha256" ]]; then
  [[ -n "$controller_pub_url" && -n "$controller_pub_sha256" ]] || die "controller_public_material_incomplete"
  command -v curl >/dev/null 2>&1 || die "curl_missing"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  curl -fsSL --proto '=https' --tlsv1.2 "$controller_pub_url" -o "$tmp" || die "controller_public_fetch_failed"
  printf '%s  %s\n' "$controller_pub_sha256" "$tmp" | sha256sum -c - >/dev/null || die "controller_public_hash_mismatch"
  controller_key="$(awk 'NF && $1 ~ /^ssh-/ {print $2; exit}' "$tmp")"
  [[ -n "$controller_key" ]] || die "controller_public_key_invalid"
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  touch "$HOME/.ssh/authorized_keys"
  chmod 600 "$HOME/.ssh/authorized_keys"
  if ! awk -v k="$controller_key" '$2==k{found=1} END{exit !found}' "$HOME/.ssh/authorized_keys"; then
    awk 'NF && $1 ~ /^ssh-/ {print; exit}' "$tmp" >> "$HOME/.ssh/authorized_keys"
  fi
fi

MACHINE_ID="$machine_id" NODE_CLASS="$node_class" HOST_NAME="$host" KEY_FP="$fingerprint" RECEIPT_PATH="$receipt_path" python3 - <<'PY'
import json, os, tempfile
from datetime import datetime, timezone
from pathlib import Path
path=Path(os.environ['RECEIPT_PATH'])
data={
  'schema':'skeleton.device_trust_enroll_receipt.v1',
  'machine_id':os.environ['MACHINE_ID'],
  'node_class':os.environ['NODE_CLASS'],
  'hostname':os.environ['HOST_NAME'],
  'key_id':'ssh-ed25519-v1',
  'public_fingerprint':os.environ['KEY_FP'],
  'public_key_path':str(Path.home()/'.config/skeleton/machine-identity/ssh_ed25519.pub'),
  'private_key_exported':False,
  'generated_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
  'status':'PENDING_CONTROLLER_VERIFICATION',
}
fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=str(path.parent))
os.close(fd)
Path(tmp).write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
os.chmod(tmp,0o600)
os.replace(tmp,path)
PY

printf 'RESULT=SUCCESS\n'
printf 'MACHINE_ID=%s\n' "$machine_id"
printf 'KEY_ID=ssh-ed25519-v1\n'
printf 'PUBLIC_FINGERPRINT=%s\n' "$fingerprint"
printf 'RECEIPT=%s\n' "$receipt_path"
printf 'PRIVATE_KEY_EXPORTED=NO\n'
printf '=== EXIT: SUCCESS ===\n'
