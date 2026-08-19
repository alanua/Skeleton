#!/usr/bin/env bash
set -euo pipefail

REPO='/home/agent/agent-dev/repos/Skeleton'
TARGET_MAIN='4dbc9e93d2a7dec1e330ce97187fb44782e6e9b6'
REPO_FULL='alanua/Skeleton'
SOURCE_ISSUE='3032'
TOKEN_CRED='/etc/skeleton/credstore.encrypted/bitwarden-access-token.cred'
GMAIL_REF_CRED='/etc/skeleton/credstore.encrypted/gmail-primary-oauth-secret-ref.cred'
RUNNER_SERVICE='skeleton-runner-poll.service'
MAIL_SERVICE='skeleton-mail-operations.service'
RUNNER_DROPIN='/etc/systemd/system/skeleton-runner-poll.service.d/30-gmail-primary-credential.conf'
MAIL_DROPIN='/etc/systemd/system/skeleton-mail-operations.service.d/30-bitwarden-gmail-primary.conf'

status='BLOCKED'
reason='unknown'
mail_binding='DEFERRED'
new_issue=''

report() {
  local body
  body=$(cat <<EOF
### Gmail Bitwarden binding operator receipt

\`\`\`text
STATUS=${status}
TARGET_MAIN=${TARGET_MAIN}
BITWARDEN_SOURCE_OF_TRUTH=true
GMAIL_REFERENCE_ENCRYPTED=$([[ -s "$GMAIL_REF_CRED" ]] && echo true || echo false)
RUNNER_CREDENTIAL_BINDING=$([[ "$status" == 'PASS' ]] && echo PASS || echo NOT_CONFIRMED)
MAIL_SERVICE_BINDING=${mail_binding}
SECRET_VALUES_PUBLISHED=NO
VAULT_ENUMERATION=NO
REASON=${reason}
NEXT_CANARY_ISSUE=${new_issue:-NONE}
\`\`\`
EOF
)
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh issue comment "$SOURCE_ISSUE" --repo "$REPO_FULL" --body "$body" >/dev/null 2>&1 || true
  fi
  printf '%s\n' "$body"
}
trap report EXIT

block() {
  reason="$1"
  exit 1
}

[[ -d "$REPO/.git" ]] || block 'runtime_repo_missing'
head_sha=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)
[[ "$head_sha" == "$TARGET_MAIN" ]] || block 'runtime_head_mismatch'
[[ -z "$(git -C "$REPO" status --porcelain --untracked-files=all 2>/dev/null)" ]] || block 'runtime_checkout_dirty'

sudo -n test -s "$TOKEN_CRED" || block 'bitwarden_access_token_encrypted_credential_missing'
command -v systemd-creds >/dev/null 2>&1 || block 'systemd_creds_missing'
command -v systemctl >/dev/null 2>&1 || block 'systemctl_missing'

runner_unit=$(sudo -n systemctl cat "$RUNNER_SERVICE" 2>/dev/null || true)
[[ -n "$runner_unit" ]] || block 'runner_service_missing'
[[ "$runner_unit" == *"LoadCredentialEncrypted=bitwarden-access-token:"* ]] || block 'runner_bitwarden_token_binding_missing'

if ! sudo -n test -s "$GMAIL_REF_CRED"; then
  if [[ -n "${GMAIL_SECRET_ID:-}" ]]; then
    if [[ ! "$GMAIL_SECRET_ID" =~ ^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$ ]]; then
      block 'gmail_secret_uuid_invalid'
    fi
    sudo -n install -d -m 700 -o root -g root /etc/skeleton/credstore.encrypted
    printf '%s' "$GMAIL_SECRET_ID" | sudo -n systemd-creds encrypt --quiet - "$GMAIL_REF_CRED" >/dev/null
    sudo -n chown root:root "$GMAIL_REF_CRED"
    sudo -n chmod 600 "$GMAIL_REF_CRED"
    unset GMAIL_SECRET_ID
  else
    block 'gmail_secret_uuid_required'
  fi
fi

# Validate encrypted reference integrity without printing or persisting the decrypted UUID.
sudo -n systemd-creds decrypt --quiet "$GMAIL_REF_CRED" - 2>/dev/null \
  | python3 -c 'import re,sys; v=sys.stdin.read().strip(); raise SystemExit(0 if re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", v) else 1)' \
  || block 'gmail_encrypted_reference_invalid'

sudo -n install -d -m 755 /etc/systemd/system/skeleton-runner-poll.service.d
printf '%s\n' \
  '[Service]' \
  "LoadCredentialEncrypted=gmail-primary-oauth-secret-ref:${GMAIL_REF_CRED}" \
  | sudo -n tee "$RUNNER_DROPIN" >/dev/null
sudo -n chmod 644 "$RUNNER_DROPIN"

if sudo -n systemctl cat "$MAIL_SERVICE" >/dev/null 2>&1; then
  sudo -n install -d -m 755 /etc/systemd/system/skeleton-mail-operations.service.d
  printf '%s\n' \
    '[Service]' \
    "LoadCredentialEncrypted=bitwarden-access-token:${TOKEN_CRED}" \
    "LoadCredentialEncrypted=gmail-primary-oauth-secret-ref:${GMAIL_REF_CRED}" \
    | sudo -n tee "$MAIL_DROPIN" >/dev/null
  sudo -n chmod 644 "$MAIL_DROPIN"
  mail_binding='PASS'
fi

sudo -n systemctl daemon-reload
runner_unit=$(sudo -n systemctl cat "$RUNNER_SERVICE" 2>/dev/null || true)
[[ "$runner_unit" == *"LoadCredentialEncrypted=gmail-primary-oauth-secret-ref:${GMAIL_REF_CRED}"* ]] \
  || block 'runner_gmail_reference_binding_not_visible'

if [[ "$mail_binding" == 'PASS' ]]; then
  mail_unit=$(sudo -n systemctl cat "$MAIL_SERVICE" 2>/dev/null || true)
  [[ "$mail_unit" == *"LoadCredentialEncrypted=bitwarden-access-token:${TOKEN_CRED}"* ]] \
    || block 'mail_bitwarden_token_binding_not_visible'
  [[ "$mail_unit" == *"LoadCredentialEncrypted=gmail-primary-oauth-secret-ref:${GMAIL_REF_CRED}"* ]] \
    || block 'mail_gmail_reference_binding_not_visible'
fi

command -v gh >/dev/null 2>&1 || block 'gh_missing'
gh auth status >/dev/null 2>&1 || block 'gh_auth_unavailable'

body_file=$(mktemp)
trap 'rm -f "$body_file"; report' EXIT
cat >"$body_file" <<EOF
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: mail_gmail_readonly_canary_v1
Repository: alanua/Skeleton
Expected Main SHA: ${TARGET_MAIN}
Account Alias: acct:gmail-primary
EOF

issue_url=$(gh issue create \
  --repo "$REPO_FULL" \
  --title 'P0 live Gmail read-only canary after Bitwarden binding' \
  --body-file "$body_file" \
  --label 'runner:ready' \
  --label 'agent:task' \
  --label 'runner:priority-1' \
  --label 'risk:yellow') || block 'canary_issue_create_failed'
rm -f "$body_file"
new_issue="#${issue_url##*/}"

# Execute one canonical Runner poll immediately; the unit receives the encrypted credentials.
sudo -n systemctl start "$RUNNER_SERVICE" || block 'runner_canary_start_failed'

status='PASS'
reason='binding_installed_canary_dispatched'
exit 0
