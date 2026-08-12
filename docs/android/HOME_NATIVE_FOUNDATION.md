# Android Home Native Foundation

This worktree adds a source-independent native Android Home foundation under `android/home`.

## Scope

- Native Kotlin and Jetpack Compose app shell.
- App display name: `Home`.
- Synthetic `OPERATOR` bottom navigation has exactly four tabs: `Головна`, `Відео`, `Пристрої`, `СК`.
- Synthetic `ORDINARY` and `SPOUSE` bottom navigation has exactly three tabs: `Головна`, `Відео`, `Пристрої`.
- `Головна` is the adaptive synthetic remote/control surface; there is no standalone remote destination.
- `СК` is an operator-only Material hub destination in the bottom navigation, not a Home screen card.
- Synthetic `OPERATOR` sessions can see and navigate to the internal native `СК` placeholder dashboard.
- Synthetic `ORDINARY` and `SPOUSE` sessions do not render `СК` in bottom navigation, and direct navigation fails closed through `canNavigateTo`.
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
