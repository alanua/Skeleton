# Clean Your TV integration for Skeleton TV

Upstream: Gathin23/cleanyourtv, commit bfe73278774116f887d77de60b782e726ba149d5. MIT copyright 2026 Gathin; preserve upstream LICENSE and launcher/FONT-LICENSE-OFL.txt.

The independent Home Edge checkout `/home/oleksii/.local/share/skeleton-tv-clean-apple-canary` contains four modifications to upstream launcher plus two new vector banners. It keeps the existing glass dock, D-pad focus and motion. Dock order: official YouTube, Skeleton Video (orange), Skeleton TV (blue). Video and TV are two virtual entries that launch `com.skeleton.tvlauncher.MainActivity` with `skeleton_tv_mode=video` or `skeleton_tv_mode=tv`. TV companion source lives in the same Home Edge at `/home/oleksii/.local/share/skeleton-tv-launcher`; its `MainActivity.kt` implements `onNewIntent` to reopen a mode when already running.

Tested locally on 2026-10-27: Gradle `testDebugUnitTest assembleRelease` successful for both projects. Launcher release APK SHA256 f7742c223e8bf3ae3b9919d45d53dd8c3c3072f18ce4a3823c2d20ab2d252c2a, companion production signed APK SHA256 dda0ffb673dc1257e66c117763640c1008148ee6301a13cc43a6fb767d466ac8. Both HTTP 200 at private Home Edge `/static/experimental/`.

No TV installation, debloat, default HOME replacement or real-hardware input test performed. Existing Sharp is not Android TV; no media playback/phone Home remote integration on a new device yet. This remains source-only experimental work, not ready to merge to main or replace production Home.
