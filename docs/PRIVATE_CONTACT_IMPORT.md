# Private Contact Import

`tools/skeleton_core/private_contact_import.py` is a Runner-side private control
command for moving contact/business-card rows from a private staging Google
Sheet into the Contact Knowledge Base V2 `RAW_IMPORT` tab.

It is intentionally metadata-only in GitHub. Task JSON must contain only sheet
ids, tab names, import mode, and dedupe policy. Contact rows, phone numbers,
email addresses, service-account JSON, OAuth tokens, and other secrets must
stay out of the repository, issues, public Runner Inbox packets, logs intended
for GitHub, and tests.

Default private paths:

- input tasks: `/home/agent/private_runner_inbox/contact_import/`
- result JSON: `/home/agent/private_runner_out/contact_import/`

Task JSON shape:

```json
{
  "staging_sheet_id": "private_google_sheet_id",
  "staging_tab": "staging_tab_name",
  "target_sheet_id": "private_google_sheet_id",
  "target_tab": "RAW_IMPORT",
  "mode": "append",
  "dedupe_policy": "skip_exact_rows"
}
```

Allowed `mode` values are `append` and `dry_run`. Allowed `dedupe_policy`
values are `skip_exact_rows` and `none`.

Run an auth-only bootstrap check:

```bash
python -m tools.skeleton_core.private_contact_import --check-only
```

Run a dry run without appending:

```bash
python -m tools.skeleton_core.private_contact_import --dry-run
```

Optionally post a public-safe GitHub status comment:

```bash
python -m tools.skeleton_core.private_contact_import \
  --post-github-status \
  --github-repo alanua/Skeleton \
  --github-issue-number 814
```

This optional path can only post a fixed status sentence:
`Private contact import: done.` or `Private contact import: blocked.` It does
not include sheet ids, tab names, row counts, contact payloads, credential
paths, or auth details.

The command prints compact JSON and writes private result JSON with one of these
machine-readable statuses:

- `AUTH_READY`
- `AUTH_MISSING`
- `AUTH_INVALID`
- `SHEET_ACCESS_MISSING`
- `IMPORT_READY`

`AUTH_MISSING` means Google Sheets credentials are not provisioned on the
runner. `AUTH_INVALID` means credentials or client libraries exist but cannot be
used. `SHEET_ACCESS_MISSING` means authorization exists but the configured
staging or target sheet is not accessible.

## Security Boundary

Google authorization cannot be invented by a model. Service-account files,
OAuth tokens, delegated access, and sheet sharing must be provisioned through a
private runner auth/bootstrap path outside chat and outside GitHub. This command
can check and report whether that private authorization is usable, but it must
not receive credentials or contact payloads through ChatGPT, GitHub issues,
public Runner Inbox packets, or committed files.

The optional `github_status` task field may request only a safe status word
(`done` or `blocked`) in private result JSON. It must not include sheet ids, tab
contents, contact rows, phone numbers, email addresses, or auth details.
