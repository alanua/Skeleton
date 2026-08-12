# Mail Operations Runtime

`scripts/mail_operations_worker.py` runs one private Gmail intake around
`core/mail_operations.py`. It keeps provider IDs, subjects, bodies, addresses,
attachments, credentials and raw Gmail payloads inside the local runtime. Public
receipts expose only provider alias, aggregate counts, status and reason codes.

The worker is Scheduler-controlled by the systemd timer cadence. Business
decisions stay in `core/mail_runtime.py`: dedupe, durable handoff, case refs,
deadline checkpoints, GitHub technical correlation, document/invoice routing and
operator packet emission.

Activation contract for a private Gmail account:

```json
{
  "schema": "skeleton.mail_provider.account.v1",
  "provider": "gmail",
  "alias": "primary",
  "credential_ref": {"kind": "env", "name": "SKELETON_GMAIL_ACCESS_TOKEN"},
  "poll_interval_seconds": 300,
  "cleanup_enabled": false,
  "label_after_handoff": "skeleton-handoff"
}
```

Canary:

```bash
python3 scripts/mail_operations_worker.py --health --account /home/agent/.config/skeleton/mail/gmail-account.json
python3 scripts/mail_operations_worker.py --once --account /home/agent/.config/skeleton/mail/gmail-account.json
```

If credentials are absent the runtime returns `AUTH_REQUIRED` without printing
secret names, token values, account identities or message contents. External
email send is not available. Cleanup is limited to explicitly classified routine
GitHub technical mail after durable private handoff and deterministic GitHub
authority correlation.
