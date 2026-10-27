Skeleton TV Launcher 0.1.0 Canary, package com.skeleton.tvlauncher.
Only read-only Home Edge APIs. Separate from working Home phone APK.
Three TV sections: YouTube app; Video history; IPTV channel catalog.
Focus/OK/Back supported through Compose. No direct TV playback yet.
No phone Home APK remote until separate paired Android TV endpoint exists.
Current endpoints only home_edge_tv and samsung_tv; do not redirect actions.
Includes Android LEANBACK_LAUNCHER and HOME intents. Does not force default.
Physical Sony/Xiaomi/Philips Android TV or emulator still needed for test.

Package visibility: official YouTube Android TV package queried explicitly.
Android TV HOME role only activates if operator selects it; no ADB default-home hack.
Source-specific banner and icon vector resources are included in GitHub.
