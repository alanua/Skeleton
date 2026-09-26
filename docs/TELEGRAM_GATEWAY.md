# Telegram Gateway

`skeleton-telegram` is a bounded gateway module, not a registered global
capability. It keeps the existing Bot API contour intact and uses
`core.telegram_notifications.send_telegram_notification` for `WRITE_BOT`.

Supported operations are limited to:

- `READ_PUBLIC`
- `READ_ALLOWED_PRIVATE`
- `WRITE_BOT`

The MTProto facade exposes read-only history access for allowlisted peers.
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

The local store is SQLite with FTS when available. It supports idempotent
upserts, resume offsets, edit updates, delete tombstones, bounded pagination,
lazy media metadata, and injectable semantic retrieval. Media metadata is stored
without default downloads.

MemoryGateway integration is proposal-only. The bridge accepts bounded
extracted facts and recommendations with Telegram provenance hashes; it rejects
raw bulk history and does not expose direct canonical writes.

Rollback note: disabling the gateway files leaves current Bot API publication
functional because `core.telegram_notifications` remains the canonical sender.
