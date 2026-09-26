# Telegram Gateway

`skeleton-telegram` is a bounded gateway module, not a registered global
capability. It keeps the existing Bot API contour intact and uses
`core.telegram_notifications.send_telegram_notification` for `WRITE_BOT`.

Supported operations are limited to:

- `READ_PUBLIC`
- `READ_ALLOWED_PRIVATE`
- `WRITE_BOT`

The typed gateway actions are `resolve_source`, `get_history`, `get_message`,
`search`, `sync_source`, `list_allowed_sources`, `watch_source`, and `write_bot`.
Every read, resolve, sync, search, and watch action emits an audit event with the
source, action, timestamp, status, count/result, and bounded error or flood-wait
state. Audit records include peer provenance hashes instead of raw message text,
session material, tokens, or secret values.

The MTProto facade exposes read-only history access. `READ_PUBLIC` accepts only
the configured source id, public handle, or stable peer id for that source.
`READ_ALLOWED_PRIVATE` accepts only exact peers listed in `allowlisted_peer_ids`.
There is no dialog enumeration, account-wide harvest, arbitrary peer search, or
implicit private access.

User-account writes and account-management operations such as send, edit,
delete, react, forward, join, leave, invite, and pin are unavailable through the
gateway.

Telethon is an optional runtime dependency. The import is lazy and happens only
when a live MTProto read is attempted without an injected test facade. If
Telethon, Home Edge secret resolution, or authorization material is unavailable,
the gateway fails closed with `AUTH_REQUIRED` or `BLOCKED`.

Secrets are provisioned only through the Home Edge app Devices -> Secrets tab,
synchronized to Bitwarden. Source contracts may reference only these material
names:

- `telegram_api_id`
- `telegram_api_hash`
- `telegram_bot_token`
- `telegram_mtproto_string_session`

No direct ChatGPT, shell, environment, or plaintext MTProto secret-entry path is
part of this gateway.

First authorization is outside ChatGPT. If phone/code/2FA is needed, the gateway
returns an auth-required boundary for the existing operator UI. After successful
authorization, the StringSession is persisted only by the Home Edge -> Bitwarden
provider and referenced as `telegram_mtproto_string_session`.

The local store is SQLite with FTS when available. It supports idempotent
upserts, resume offsets, edit updates, delete tombstones, bounded pagination,
lazy media metadata, and injectable semantic retrieval. Media metadata is stored
without default downloads. `sync_source` keeps a durable per-source cursor and a
small reconciliation overlap so restarts can observe edits without duplicate
message rows. Flood waits are returned and audited without advancing the cursor.

Normalized message rows retain Telegram peer/channel id, username, title,
message id, sent/edit/delete timestamps, sender or author metadata, text,
entities, extracted URLs, reply and thread ids, forwarded-source metadata, media
type and caption, and Telegram permalinks when derivable. Search supports FTS
and injectable semantic retrieval with source/date/limit filters and returns the
same normalized source, date, message id, and permalink metadata.

MemoryGateway integration is proposal-only. The bridge accepts bounded
extracted facts and recommendations with Telegram provenance hashes; it rejects
raw bulk history and does not expose direct canonical writes. Raw Telegram posts
are not copied into MemoryGate, receipts, audit, or repository artifacts.

Media downloads are not part of default read, search, or sync actions. Any media
download feature must be a separate explicit bounded operation.

Rollback note: disabling the gateway files leaves current Bot API publication
functional because `core.telegram_notifications` remains the canonical sender.
