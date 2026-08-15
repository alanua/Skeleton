# Mail Operations

Mail Operations runs as a provider-neutral worker behind Skeleton Scheduler. The
Scheduler database is the only authority for due work. Mail provider cursors and
message processing state are idempotency records only; they do not decide when
work is due.

## Runtime

The scheduler route is `workflow:mail.poll_provider`. A schedule payload carries
a typed `skeleton.mail_poll_packet.v1` account reference and policy envelope.
`MailRuntime` dispatches that packet, asks the configured provider adapter for
normalized `MailEnvelope` objects, processes each envelope through
`core.mail_operations`, and writes a public-safe receipt.

Gmail is the first active adapter, implemented at `adapters/gmail_mail_provider.py`.
When `provider` is `gmail` and no provider fixture is supplied, the worker uses
the local `GmailMailProvider`. Fixture mode remains deterministic and synthetic:
any `--provider-fixture` selects `StaticMailProvider` even for a Gmail account
record. The adapter performs read-only `messages.list` and `messages.get`
metadata reads; it does not send, delete, label, archive, mark read, or schedule
mail.

## Gmail OAuth

Gmail credentials are operator-local private files, keyed by opaque
`account_ref`. The account metadata file contains only scheduler/provider
configuration:

```json
{
  "schema": "skeleton.mail_provider_account.v1",
  "account_ref": "acct:gmail-primary",
  "provider": "gmail",
  "poll_interval_seconds": 300,
  "max_messages_per_poll": 10,
  "query": "label:important newer_than:7d"
}
```

The private credential root defaults to
`/var/lib/skeleton/mail/gmail` under systemd via
`SKELETON_GMAIL_CREDENTIAL_ROOT`. Directory permissions must be `0700`; bundle
permissions must be `0600`. Missing, invalid, over-permissive, or revoked
credentials fail closed with public-safe reason codes such as
`GMAIL_CREDENTIAL_MISSING`, `GMAIL_CREDENTIAL_INVALID`,
`GMAIL_CREDENTIAL_PERMISSIONS_INVALID`, `GMAIL_CREDENTIAL_REVOKED`, or
`GMAIL_OAUTH_SCOPE_INVALID`. Receipts must not include client secrets, refresh
tokens, access tokens, email addresses, or mail payloads.

Minimum OAuth scope:
`https://www.googleapis.com/auth/gmail.readonly`.

Multiple Gmail accounts are supported by running onboarding once per distinct
`account_ref`. Each account gets an independent private credential bundle.

## Gmail Activation Contract

Code generation and tests must not contact Gmail. Operators activate Gmail
locally in this order:

1. Install the worker and systemd unit:
   `sudo scripts/install_mail_operations_worker.sh`
2. Prepare private local files containing the OAuth client id, OAuth client
   secret, and one-time authorization code. Do not place those files in Git.
3. Generate the account authorization URL:
   `/opt/skeleton-mail-operations/gmail_oauth_authorize.py --account-ref acct:gmail-primary --client-id-file /private/client_id --client-secret-file /private/client_secret url`
4. Complete consent for the intended Gmail account using the read-only scope.
   The default loopback redirect is `http://127.0.0.1:8765/oauth2callback`;
   if no local listener is running, copy the `code` value from the browser's
   redirected URL into the private code file.
5. Exchange the one-time code into the private credential bundle:
   `/opt/skeleton-mail-operations/gmail_oauth_authorize.py --account-ref acct:gmail-primary --client-id-file /private/client_id --client-secret-file /private/client_secret exchange --code-file /private/code`
6. Register the Skeleton Scheduler poll from the account metadata file:
   `/opt/skeleton-mail-operations/mail_operations_worker.py --account /var/lib/skeleton/mail/account.json register`
7. Run one read-only canary tick and inspect only the public-safe receipt:
   `/opt/skeleton-mail-operations/mail_operations_worker.py --account /var/lib/skeleton/mail/account.json tick --max-dispatches 1`
8. Enable the timer only after the canary receipt is accepted:
   `sudo systemctl enable --now skeleton-mail-operations.timer`

## Operator Handoff

Important mail produces a Ukrainian operator packet. Telegram handoff is kept
behind `integrations/mail_telegram.py`, which builds typed callback packets and
stable idempotency keys. Code generation must not perform live Telegram sends.

## Policy

Invoice mail and technical mail are explicit policy categories:

- `mail.invoice.v1` routes invoice/rechnung/payment mail to operator review.
- `mail.technical.v1` routes incident/outage/error mail to operator review.
- general important/deadline mail routes through `mail.important.v1`.

All receipts are public-safe and assert that no live external side effects were
executed.
