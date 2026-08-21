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

Bootstrap discovery is separated from activation. The helper
`scripts/bitwarden_gmail_primary_reference_helper.py` reads the existing
`bitwarden-access-token` systemd credential, exchanges that machine token only
against the fixed Bitwarden identity endpoint, derives the organization id from
the identity token contract, and invokes the pinned `bitwarden-sdk==2.1.0`
Python SDK in an isolated interpreter. Discovery uses only the official
`SecretsClient.list(organization_id)` identifier surface and matches the fixed
Gmail-primary identifier metadata. It never reads a secret value and never writes
under `CREDENTIALS_DIRECTORY`; stdout contains only public status and
zero/one/many booleans. When exactly one match exists, the opaque UUID may be
returned to the parent over an explicitly supplied in-memory file descriptor so
the parent can persist the encrypted `skeleton-secret-reference-index`
credential through the existing systemd credential store.

Installing the worker copies code and units but leaves
`skeleton-mail-operations.timer` disabled. Activation is a separate registered
maintenance operation, `mail_gmail_primary_registered_activation_v1`, after
operator-reviewed exact-main runtime sync and service credential reload. It
performs exact-main preflight, rereads the registered opaque reference from the
canonical `skeleton-secret-reference-index` systemd credential, runs exactly one
read-only Gmail canary, and only on canary pass enables/starts the canonical
worker timer and verifies bounded systemd health.

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
