#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "${1:-.}" && pwd -P)"
run_as_user="${2:-agent}"
runtime_root="/usr/local/lib/skeleton/family-document-runtime"
state_root="/var/lib/skeleton/family-documents"
inbox_root="$state_root/inbox"
archive_root="$state_root/archive"
outbox_root="$state_root/outbox"
private_memory_root="/home/$run_as_user/.local/share/skeleton-private-memory"
scheduler_root="/var/lib/skeleton/scheduler"

case "$repo_root" in
  /tmp/*|/var/tmp/*) echo "repository root must be durable" >&2; exit 2 ;;
esac
[[ "$run_as_user" == "agent" ]] || { echo "family document worker must run as canonical agent user" >&2; exit 2; }
id -u "$run_as_user" >/dev/null 2>&1 || { echo "canonical agent user is unavailable" >&2; exit 2; }

for required in \
  core/family_document_calendar.py \
  core/family_document_intake.py \
  core/family_document_local_inference.py \
  core/family_document_report.py \
  core/family_document_runtime.py \
  core/family_document_sinks.py \
  core/family_document_sources.py \
  core/family_document_state.py \
  core/family_document_taxonomy.py \
  core/home_edge/family_document_production.py \
  core/local_document_ocr.py \
  scripts/family_document_intake.py \
  scripts/family_document_worker.py \
  ops/systemd/skeleton-family-document-intake.service; do
  [[ -f "$repo_root/$required" ]] || { echo "required source missing: $required" >&2; exit 2; }
done

for required_tool in /usr/bin/pdftotext /usr/bin/pdfinfo /usr/bin/tesseract /usr/bin/ocrmypdf /usr/bin/libreoffice; do
  [[ -x "$required_tool" ]] || { echo "local OCR dependency unavailable: $required_tool" >&2; exit 2; }
done

install_root=(install)
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  install_root=(sudo install)
fi

"${install_root[@]}" -d -m 0755 /etc/skeleton /usr/local/lib/skeleton "$runtime_root/core/home_edge" "$runtime_root/scripts"
"${install_root[@]}" -d -o "$run_as_user" -g "$run_as_user" -m 0700 "$inbox_root" "$archive_root" "$outbox_root" "$private_memory_root"
"${install_root[@]}" -d -o "$run_as_user" -g "$run_as_user" -m 0700 "$scheduler_root"
"${install_root[@]}" -m 0644 /dev/null "$runtime_root/scripts/__init__.py"
while IFS= read -r source_file; do
  relative_path="${source_file#"$repo_root"/}"
  "${install_root[@]}" -D -m 0644 "$source_file" "$runtime_root/$relative_path"
done < <(find "$repo_root/core" -type f -name '*.py' -print)
"${install_root[@]}" -m 0644 \
  "$repo_root/scripts/family_document_intake.py" \
  "$repo_root/scripts/family_document_worker.py" \
  "$runtime_root/scripts/"
"${install_root[@]}" -m 0644 "$repo_root/ops/systemd/skeleton-family-document-intake.service" \
  /etc/systemd/system/skeleton-family-document-intake.service

systemctl_cmd=(systemctl)
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  systemctl_cmd=(sudo systemctl)
fi
"${systemctl_cmd[@]}" daemon-reload

echo 'INSTALLED_NOT_STARTED'
