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
The adapter normalizes Gmail message metadata and snippets; it does not send,
delete, label, archive, or schedule mail.

Gmail OAuth material is resolved only through the provider-neutral
`CredentialBroker` with the Bitwarden backend. For `acct:gmail-primary`, runtime
must first provide exactly one opaque Bitwarden reference in the encrypted
systemd credential `skeleton-secret-reference-index` using schema
`skeleton.secret_reference_index.v1`. If that registration is absent or
ambiguous, normal worker credential resolution fails closed with
`REFERENCE_BOOTSTRAP_REQUIRED` or `REFERENCE_REGISTRATION_AMBIGUOUS`; the worker
must not query vault projects, use `bws secret list`, export vault data, or read
unrelated values. The actual OAuth bundle remains in Bitwarden and is delivered
only ephemerally to the Gmail provider.

The activation task may bootstrap a missing `acct:gmail-primary` index entry
from the existing `bitwarden-access-token` systemd credential. It does not
require or assume a separate `bitwarden-organization-id` credential. The helper
installed at `/opt/skeleton-mail-operations/bitwarden_gmail_reference_helper.py`
runs under the pinned `/opt/skeleton-bitwarden-sdk-runtime/venv/bin/python`
runtime, exchanges the machine token only with
`https://identity.bitwarden.com/connect/token`, extracts only the organization
claim from the returned JWT, and performs one metadata-only secret identifier
listing against the fixed Bitwarden API host. Zero or multiple Gmail matches
block activation; exactly one match is encrypted with `systemd-creds` into the
existing reference-index credential and writes only the opaque UUID into the
current credential directory for the immediate reread.

Installing the worker copies code and units but leaves
`skeleton-mail-operations.timer` disabled. Activation is a separate registered
maintenance operation, `mail_gmail_primary_registered_activation_v1`, after
operator-reviewed exact-main runtime sync. It performs exact-main preflight,
binds the registered opaque reference, runs exactly one read-only Gmail canary,
and only on canary pass enables/starts the canonical worker timer and verifies
bounded systemd health.

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
