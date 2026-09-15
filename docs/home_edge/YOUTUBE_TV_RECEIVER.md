# Home Edge YouTube TV receiver

`youtube_tv_receiver` is a bounded receiver capability for phone/tablet control from the native YouTube app. It is distinct from ordinary `youtube_web`, which remains part of the persistent Chrome/MPV media stack.

The receiver contract reuses only the historical Waydroid Android-TV YouTube surface: launch the official `com.google.android.youtube.tv` app in the bounded `waydroid_android_tv_youtube` surface, let that official app provide pairing/cast behavior, and close any transient ADB server after setup or launch. It does not implement private YouTube Lounge or Cast protocols, does not resurrect AirScreen, and does not make Android a generic media authority.

Public status is limited to:

- `receiver_available`
- `receiver_running`
- `pairing_ready`
- `display_owner`
- `stable_reason`

Pairing codes, accounts, cookies, device serials, and app profile paths remain private local profile state and are not emitted in receipts or tests.

## Lifecycle

Enter is idempotent. If the receiver is already running exactly once, Home Edge only reasserts `display_owner=youtube_tv_receiver`. If no receiver is running, the backend launches the official YouTube TV app surface once, assigns display ownership to the receiver, and closes ADB afterward.

Exit stops the bounded receiver instance, restores the Chrome/MPV architecture, preserves the current volume, and sets display ownership back to the desktop stack. HyperHDR and screensaver ownership continue to use the media display ownership guard.

## Migration note

Reused: the previously working Waydroid Android-TV YouTube app surface and Android media-session observation for the narrow receiver mode.

Still removed/unsupported: AirScreen, VLC revival as a receiver path, persistent ADB listeners or watchdogs, generic Android media mode authority, router/firewall mutation, credentials in source/tests/logs, and any reverse-engineered YouTube or Cast protocol implementation.
