#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: install_family_document_worker.sh SOURCE_ROOT USER_UNIT_DIR" >&2
  exit 2
fi

SOURCE_ROOT=$1
USER_UNIT_DIR=$2
UNIT_SOURCE="$SOURCE_ROOT/ops/systemd/skeleton-family-document-intake.service"
UNIT_TARGET="$USER_UNIT_DIR/skeleton-family-document-intake.service"

test -f "$UNIT_SOURCE"
mkdir -p "$USER_UNIT_DIR"
chmod 700 "$USER_UNIT_DIR"
TEMP="$UNIT_TARGET.part"
cp "$UNIT_SOURCE" "$TEMP"
chmod 600 "$TEMP"
mv "$TEMP" "$UNIT_TARGET"

# Deliberately does not enable or start the unit.
printf '%s\n' '{"schema":"skeleton.family_document.install_receipt.v1","status":"INSTALLED_DISABLED","service_enabled":false,"service_active":false}'
