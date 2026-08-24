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
ambiguous, credential resolution fails closed with `REFERENCE_BOOTSTRAP_REQUIRED`
or `REFERENCE_REGISTRATION_AMBIGUOUS`; the worker must not query vault projects,
list secrets, export vault data, or read unrelated values. The actual OAuth
bundle remains in Bitwarden and is delivered only ephemerally to the Gmail
provider.

Reference bootstrap is isolated from the long-running Runner Python. The
registered activation command launches only
`/opt/skeleton-bitwarden-sdk/bin/python` against the fixed helper installed at
`/opt/skeleton-bitwarden-sdk/bitwarden_secret_identifier_helper.py`. That venv
contains the pinned official `bitwarden-sdk==2.1.0`; the Runner process does not
import `bitwarden_sdk`. The helper authenticates from the private
`bitwarden-access-token` credential, receives organization identity from the
private `bitwarden-organization-id` credential, calls only the SDK identifier
list surface, and fails closed on zero or many code-owned key matches. Public
activation receipts expose only `identifier_bootstrap` status and zero/one/many
match state, never token, key, organization id, project id, or selected UUID.

Installing the worker copies code and units but leaves
`skeleton-mail-operations.timer` disabled. Activation is a separate registered
maintenance operation, `mail_gmail_primary_registered_activation_v1`, after
operator-reviewed exact-main runtime sync. It performs exact-main preflight,
discovers and stores one opaque Bitwarden reference in the existing encrypted
reference index, rereads that reference through the runtime credential boundary,
runs exactly one read-only Gmail canary, and only on canary pass enables/starts
the canonical worker timer and verifies bounded systemd health.

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
