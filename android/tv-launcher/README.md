# Skeleton TV — Apple-style media launcher canary

Experimental **two-package** Android TV implementation, built from the upstream
[Clean Your TV](https://github.com/Gathin23/cleanyourtv) shell at exact commit
`bfe73278774116f887d77de60b782e726ba149d5`.

It does **not** replace the working Skeleton Home APK, physical Sharp TV media
player, Home Edge runtime, registry devices, or original Clean Your TV sources.

## Two cooperating APKs

1. **Skeleton TV Launcher**: Android package `com.skeleton.tvhome`. Clean Your TV
   native launcher, with its existing D-pad navigation, cinematic focus shelf,
   Apple TV-like glass dock, smooth highlight animations and three columns.
   Stock launcher remains enabled. The APK only becomes Home after explicit
   selection on a compatible Android TV.
2. **Skeleton TV Media 0.2.0**: Android package `com.skeleton.tvlauncher`.
   Contains two separately exported TV launcher activities:
   `VideoEntry` ("Відео") and `TvEntry` ("ТВ"), under the same package and shared
   code/data. The generic `MainActivity` is private and no longer claims HOME.
   Reads from the existing Home Edge `/api/video/history` and `/api/tv/channels`.
   Preserves hidden-video filtering, shared canonical media IDs, posters and
   IPTV EPG. Does not create a new library or playback history.

YouTube uses the already installed official Android TV YouTube app. The Clean
Your TV dock uses `dock_apps`: YouTube followed by the companion package.
Its existing Java `AppAdapter` retains distinct Leanback activities with
different labels, so the companion produces **two separate tiles** in the
second and third dock slots. Configure four apps only if the operator requests.

## Source and license

The upstream shell is MIT-licensed and uses the Outfit font under SIL OFL.
Build from the exact upstream SHA using
`clean-apple/build-from-clean-your-tv.sh`, which applies the tracked dock,
branded application label and source overlay. Its APK embeds the licenses in
`assets/THIRD_PARTY_NOTICES.txt`. Never replace upstream copyright notices.

The script builds locally only. It does **not** run ADB, install APKs, disable
stock apps, open network access, turn on debugging or deploy to Home Edge.

## Tested canary artifacts (27 October 2026)

- Skeleton TV Apple Launcher: `com.skeleton.tvhome`, SHA256
  `6de024fd4635cae7572406f55e713e083263a59d7e58a485c6643334dac942b2`.
  HTTP path `/static/experimental/Skeleton-TV-Apple-Launcher-20261027.apk`.
- Skeleton TV Media 0.2.0: `com.skeleton.tvlauncher`, SHA256
  `adb4cc184fe9f3a09fff9cdb5035bdf4481cabb613c49ee317a6164b676ee8f8`.
  HTTP path `/static/experimental/Skeleton-TV-Media-0.2.0.apk`.

Both locally passed Android JUnit tests, release build and signing; both HTTP
downloads independently returned 200 with verified content length. The
production mobile Home.apk remains exact Home 1.4.51 SHA256
`8e6764b7c22270ebb5b50d61a35288c7f164793b9a5a3cb362a79fd68a7fa89d`.

## Not yet delivered

- No physical Android TV/emulator D-pad and HOME-role acceptance test.
- No local TV Media3 playback of video/IPTV: the existing endpoints currently
  expose catalog state, while direct TV-safe streaming still requires an
  independently registered and authenticated Android TV endpoint.
- No paired Home APK control of the future Android TV; never redirect commands
  to existing `home_edge_tv` or `samsung_tv` endpoints.
- No automatic debloat. The Clean Your TV upstream repository's disabling and
  restore scripts must only be used with specific device verification, backup,
  and explicit operator approval after purchasing/connecting a real TV.

Install the **media companion first**, then the launcher, when a compatible
owned Android TV is available. Verify the native remote and stock-home rollback
before choosing the custom HOME role. Physical device changes must go through
canonical Skeleton Home Edge registered operations, approvals and audit.
