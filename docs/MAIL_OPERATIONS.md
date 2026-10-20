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

## Security Triage

`core.mail_security` adds a provider-neutral, side-effect-free risk assessment
layer. It evaluates only normalized envelope text hints and optional metadata
from adapters, then emits typed public-safe categories:
`ORDINARY`, `ACTIONABLE`, `INVOICE_PAYMENT`, `TECHNICAL`, `SPAM`, `PHISHING`,
`SCAM`, `PSEUDO_INKASSO`, `IDENTITY_MISUSE_SUSPECTED`, and
`OFFICIAL_LEGAL_NOTICE`.

Authentication results are evidence only. SPF/DKIM/DMARC `PASS` never marks a
sender trustworthy by itself. When metadata is available, the layer compares
sender, display, reply-to, contact, and payment domains and records only stable
reason codes, not the private values. Claimed contracts, orders, payments, and
identity misuse generate a bounded private evidence-search request over existing
case/correspondence/document history. Known-risk hooks are represented as a
bounded evidence request contract; the mail worker receives no browser or
arbitrary web authority.

Suspicious phishing, scam, fake-Inkasso, identity-abuse, and official legal
notice findings route through the existing `NEEDS_OPERATOR` flow with a private
case update containing evidence refs only. Those packets intentionally expose
investigation actions instead of reply approval, so the worker never clicks
links, pays, replies, acknowledges debt, accepts/cancels contracts, mutates the
mailbox, or files police/regulator/DSGVO submissions. Routine low-risk spam is
ignored and produces no Telegram handoff. Genuine official/legal uncertainty is
priority operator work and is never silently treated as spam.

All receipts are public-safe and assert that no live external side effects were
executed.
