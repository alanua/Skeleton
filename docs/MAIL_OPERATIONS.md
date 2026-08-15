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
