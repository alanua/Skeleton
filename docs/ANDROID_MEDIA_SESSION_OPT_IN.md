# Android Media Session Opt-In

Home's media-session snapshot path is source-only and inert unless the user explicitly enables the app's notification-listener component in Android system settings.

The Android app does not request `android.permission.MEDIA_CONTENT_CONTROL`. Instead, `MediaSessionSnapshotAdapter` first requires a non-empty local allowlist of media package names, then verifies that `.MediaSessionObservationListener` is present in `Settings.Secure.ENABLED_NOTIFICATION_LISTENERS`, and only then calls `MediaSessionManager.getActiveSessions(ComponentName(...))`.

`.MediaSessionObservationListener` exists only so Android can authorize the `ComponentName` used for media-session observation. It does not implement notification callbacks, inspect notification extras, read notification payloads, launch settings, mutate playback, or install/enable itself.

Snapshot output remains local-private and minimal: playback status, position, duration, redacted title, and collection time. Package names and notification data are not emitted. This draft PR does not claim the listener is enabled on any phone.
