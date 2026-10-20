# Scan Report Manifest v1

`scan_report_manifest.json` is the source of truth for Brother/MFP scan reporting. Telegram, web, and email renderers must read this manifest; they must not decide document boundaries, classification, storage, or verification.

The v1 flow is:

1. scan and session assembly finish
2. document boundaries and classification are produced by the existing conveyor/local inference path
3. per-document originals and searchable PDFs are written to private storage
4. each reported PDF is opened, page-count checked, hashed, and linked through the private download provider
5. `scan_report_manifest.json` is persisted
6. Telegram delivery reads only the manifest and records message IDs/delivery state in the scan report SQLite store

Telegram output shows only human storage paths such as `owner/documents/topic/year`. Canonical filesystem paths remain in the manifest/audit record and are never rendered into Telegram text.
Delivery receipts are public-safe: they contain scan/session/document/manifest/artifact IDs, manifest and artifact SHA-256 values, channel status, message IDs, retry state, and stable reason codes. They do not contain document text, customer names, private paths, private links, tokens, cookies, or credentials.

Operations:

- Build: `python3 scripts/scan_report.py build --input package.json --output scan_report_manifest.json`
- Inspect: `python3 scripts/scan_report.py inspect --manifest scan_report_manifest.json`
- Resend/retry: `python3 scripts/scan_report.py resend --manifest scan_report_manifest.json --state-db /private/state/scan_reports.sqlite`

Secrets are read from environment/secret store only:

- `SKELETON_PRIVATE_DOWNLOAD_BASE_URL`
- `SKELETON_PRIVATE_DOWNLOAD_LINK_SECRET`
- `SKELETON_TG_BOT`
- `SKELETON_TG_CHAT`

Replay behavior is keyed by `session_id + manifest_version + manifest_sha` for the package delivery and by `session_id + document_id + manifest_version + manifest_sha` in per-document receipt entries. Replaying an unchanged finalized manifest after restart does not send duplicate Telegram messages. A changed manifest for the same session/version creates a new SHA-bound delivery key, supersedes the previous delivery state, and preserves the previous audit record.
