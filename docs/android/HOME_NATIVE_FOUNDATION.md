# Android Home Native Foundation

This worktree adds a source-independent native Android Home foundation under `android/home`.

## Scope

- Native Kotlin and Jetpack Compose app shell.
- App display name: `Home`.
- Synthetic `OPERATOR` bottom navigation has exactly four tabs: `Головна`, `Відео`, `Пристрої`, `СК`.
- Synthetic `ORDINARY` and `SPOUSE` bottom navigation has exactly three tabs: `Головна`, `Відео`, `Пристрої`.
- `Пульт` is a contextual/direct route and is not a fourth bottom tab.
- `СК` is an operator-only Material hub destination in the bottom navigation, not a Home screen card.
- Synthetic `OPERATOR` sessions can see and navigate to the internal native `СК` operator dashboard.
- Synthetic `ORDINARY` and `SPOUSE` sessions do not render `СК` in bottom navigation, and direct navigation fails closed through `canNavigateTo`.
- The `СК` dashboard reads the public-safe Home Edge operator live-state endpoint and renders only simple primary Ukrainian sections: `Працює зараз`, `Чекає`, `Потрібна моя увага`, `Щойно завершено`, `Далі`.
- Failed dashboard reads render stale/offline state instead of showing cached data as fresh.
- The endpoint treats the canonical GitHub/control-plane Runner queue snapshot as queue truth; Scheduler occurrences are supplementary only and missing/partial live state fails closed.
- There are no credentials, provider mutations, raw GitHub payloads, private runtime paths, or technical IDs in the primary UI.

## Interfaces Waiting For Home Device Sources

These typed interfaces are intentionally present but backed only by placeholders until Home device sources are connected:

- `CanonicalHomeApi`
- `OperatorDashboardRepository`
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
