# Android Home Native Foundation

This worktree adds a source-independent native Android Home foundation under `android/home`.

## Scope

- Native Kotlin and Jetpack Compose app shell.
- App display name: `Home`.
- Primary bottom navigation has exactly three tabs: `Головна`, `Відео`, `Пристрої`.
- `Пульт` is a contextual/direct route and is not a fourth bottom tab.
- `СК` is an operator-only entry on the Home screen using the Material `Hub` icon.
- Synthetic `OPERATOR` sessions can see and navigate to `СК`.
- Synthetic `ORDINARY` and `SPOUSE` sessions do not render `СК`, and direct navigation fails closed through `canNavigateTo`.
- Placeholder state is synthetic only. There are no live Home values, endpoints, credentials, device identifiers, or Home Edge calls.

## Interfaces Waiting For Canonical Live Source

These typed interfaces are intentionally present but backed only by placeholders until #2343 defines the canonical live source:

- `CanonicalHomeApi`
- `AuthSessionProvider`
- `ConnectivityMonitor`
- `SecureStorage`
- `VerifiedActionStateStore`

The connectivity contract preserves `ONLINE`, `DEGRADED`, and `OFFLINE`.
The verified action-state contract preserves `SENT`, `ACCEPTED`, `APPLIED`, and `PHYSICALLY_VERIFIED`.

## Build And Validation

When normal Android tooling is available:

```bash
cd android/home
gradle :app:assembleDebug
gradle :app:testDebugUnitTest
```

Repository-level contract validation that does not require Android tooling:

```bash
pytest tests/android
git diff --check
```
