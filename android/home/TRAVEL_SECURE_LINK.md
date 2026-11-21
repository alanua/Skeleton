# Source-only S20 Travel secure link

`TravelSecureLink.kt` connects a remote client to the existing authoritative
`TravelMediaSession` using authenticated encrypted request/response records.
It does not activate networking, discovery, pairing, or any Home UI. No TV,
tablet, existing remote, application version, or manifest changes are required.

An approved future foreground LAN adapter supplies `TravelRecordExchange`.
It must accept complete records only, enforce the 65536-byte limit before
allocation, apply connection/read deadlines, and dispatch host exchanges to the
session's owning thread (the Android display requires the main thread).
Do not call blocking network operations on that thread. The adapter must close
the host when disconnected or when its foreground owner stops. Client close
clears local link keys; it does not implicitly notify the host.

Provision a fresh random 32-byte peer key and unique link ID for each connection
through a separately approved authenticated pairing boundary. Neither is
provisioned or persisted by this code. The key authenticates its holder, so it
must be restricted to the intended peer; this is not a public-key identity or
password protocol. Each channel derives separate host/client AES-256-GCM keys
with HMAC-SHA256 and binds protocol version, link ID, and direction as AAD.
Counters reject replay and reordered records. Authentication failures cannot
advance receive state. Invalid records terminate the host/client link; callers
must not retry commands with uncertain outcomes using a new connection.

Construct a host channel with `host = true`, and a client channel with
`host = false`, using the same provisioned key and link ID. Construct
`TravelSecureHost(session, hostChannel)` and pass its byte exchange through the
adapter to `TravelSecureClient(sessionId, clientChannel, exchange)`.
Remote execution remains disabled until an explicit local
`session.allowRemoteControl(true)`. Decoded commands always have REMOTE origin;
normal session, revision, media, HDMI, and player guards still apply. Responses
carry encrypted command results and authoritative state. Host close revokes
remote control without stopping local playback. Use one host per session and
one serialized request in flight per client. No unsolicited state subscription
is provided; callers obtain the latest authoritative state in each response.

Validation: `python3 -m unittest discover -s tests/android -p 'test_travel*py'`
checks source boundaries only. `TravelSecureLinkTest` contains synthetic,
in-memory JVM round trips and negative crypto/protocol scenarios. Run it with
the Android unit-test toolchain before integration; that toolchain is absent
from the source-only runner. No sockets or real devices are used by these tests.
