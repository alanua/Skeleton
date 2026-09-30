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
`READ_ALLOWED_PRIVATE` accepts only exact peers listed in
`allowlisted_peer_ids`; stable peer ids work even when the public handle is not
allowlisted. There is no dialog enumeration, account-wide harvest, arbitrary
peer search, or implicit private access.

User-account writes and account-management operations such as send, edit,
delete, react, forward, join, leave, invite, and pin are unavailable through the
gateway.

Telethon is an optional runtime dependency. The import is lazy and happens only
when a live MTProto read is attempted without an injected test facade. The
facade connects an already-authenticated `StringSession`, supports Telethon
awaitables and async iterators from the synchronous gateway API, and checks that
the user session is authorized before any read. If Telethon, Home Edge secret
resolution, session authorization, or bounded API access is unavailable, the
gateway fails closed with `AUTH_REQUIRED`, `FLOOD_WAIT`, or `BLOCKED`.
Gateway construction accepts only narrow provider callbacks for this live path:
`secret_resolver(ref)`, `authorization_provider(source, refs)`, and
`session_persist(source, string_session)`. These callbacks are supplied by the
Home Edge -> Bitwarden credential boundary; callers do not pass raw MTProto
secrets into action methods, and the gateway never falls back to shell
environment, ChatGPT text, or plaintext config values.

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
may invoke only the narrow Home Edge Devices -> Secrets tab provider callback
for the allowed material refs above. After successful authorization, the
StringSession is persisted only by the Home Edge -> Bitwarden provider and
referenced as `telegram_mtproto_string_session`; existing sessions read without
prompting again.

`resolve_source` performs bounded live peer resolution when an MTProto facade or
the provider-backed default facade is available. It resolves only the configured
handle, source id, stable peer id, or exact allowlisted peer; it does not list
dialogs. The returned source record includes normalized peer/channel id,
username, handle, and title from Telegram. Without live auth/client access it
returns an audited `AUTH_REQUIRED`, `FLOOD_WAIT`, or `BLOCKED` result instead of
claiming config-only source resolution as a live Telegram resolution.

The local store is SQLite with FTS when available. It supports idempotent
upserts, resume offsets, edit updates, delete tombstones, bounded pagination,
lazy media metadata, and injectable semantic retrieval. Media metadata is stored
without default downloads. `sync_source` first consumes a fake or
Telethon-compatible update seam for new messages, edits, and deleted-message
tombstones, then falls back to bounded overlap reads for reconciliation. Cursor
and update-marker writes happen with the imported rows/tombstones, so restarts
retry safely and flood waits are returned and audited without advancing state.

The `@midnightquantum` public integration harness must use the same public
gateway path as production reads. With an injected deterministic client it
resolves the configured source, imports a bounded recent set, stores normalized
metadata, and proves search over the imported rows. Without live credentials or
the optional dependency it returns `AUTH_REQUIRED` or `BLOCKED`; it does not
claim `DONE` unless at least one message was read.

Normalized message rows retain Telegram peer/channel id, username, title,
message id, sent/edit/delete timestamps, sender or author metadata, text,
entities, extracted URLs, reply and thread ids, forwarded-source metadata, media
type and caption, and Telegram permalinks when derivable. Search supports FTS
and injectable semantic retrieval with source/date/limit filters and returns the
same normalized source, date, message id, and permalink metadata.

MemoryGateway integration is proposal-only. The bridge accepts bounded
extracted facts and recommendations with Telegram message reference, sent date
when available, permalink when available, confidence, and provenance hashes. It
rejects raw bulk history and does not expose direct canonical writes. Raw
Telegram posts are not copied into MemoryGate, receipts, audit, or repository
artifacts.

Media downloads are not part of default read, search, or sync actions. Any media
download feature must be a separate explicit bounded operation.

Rollback note: disabling the gateway files leaves current Bot API publication
functional because `core.telegram_notifications` remains the canonical sender.
