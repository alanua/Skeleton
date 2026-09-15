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

Installing the worker copies code and units but leaves
`skeleton-mail-operations.timer` disabled. Activation is a separate registered
maintenance operation, `mail_gmail_primary_registered_activation_v1`, after
operator-reviewed exact-main runtime sync. It performs exact-main preflight,
verifies the isolated pinned Bitwarden SDK runtime, runs the identity metadata
bootstrap, rereads the encrypted reference index, runs exactly one read-only
Gmail canary, and only on canary pass enables/starts the canonical worker timer
and verifies bounded systemd health.

The bootstrap helper reads `bitwarden-access-token` only from the private
systemd credential directory, parses the Bitwarden Secrets Manager machine token
in memory, and performs one HTTPS form POST to the code-owned Bitwarden identity
`/connect/token` endpoint with `grant_type=client_credentials` and
`scope=api.secrets`. It validates the returned Bearer JWT locally, logs no token
component, then logs the pinned SDK in with the original machine token and calls
only `client.secrets().list(organization_id)` to discover secret identifier
metadata. The only accepted key is
`skeleton/mail-gmail/acct:gmail-primary/oauth-readonly`; exactly one UUID match
is piped directly to `systemd-creds encrypt` for
`skeleton-secret-reference-index`. The public helper receipt is limited to
status, match-count class, persisted, and reason enums.

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
