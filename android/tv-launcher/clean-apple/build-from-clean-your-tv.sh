#!/usr/bin/env bash
# Source-only, pinned Clean Your TV build. Does NOT connect to a TV,
# run ADB, disable Android packages, install or change a launcher.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORKDIR="${1:-$PWD/skeleton-tv-clean-worktree}"
UPSTREAM_SHA=bfe73278774116f887d77de60b782e726ba149d5

if [ -e "$WORKDIR" ]; then
  echo "Refusing existing target: $WORKDIR" >&2
  exit 2
fi
git clone --no-checkout https://github.com/Gathin23/cleanyourtv.git "$WORKDIR"
git -C "$WORKDIR" fetch --depth=1 origin "$UPSTREAM_SHA"
git -C "$WORKDIR" checkout --detach "$UPSTREAM_SHA"
test "$(git -C "$WORKDIR" rev-parse HEAD)" = "$UPSTREAM_SHA"

cd "$WORKDIR/launcher"
cp "$HERE/overrides/gradle.properties" ./gradle.properties
cp "$HERE/overrides/app/src/main/res/values/config.xml" ./app/src/main/res/values/config.xml

: "${ANDROID_HOME:?Install Android SDK and set ANDROID_HOME}"
: "${JAVA_HOME:?Install JDK 17 and set JAVA_HOME}"
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > local.properties
./gradlew --no-daemon --console=plain testDebugUnitTest assembleRelease

printf 'Unsigned-for-Skeleton signing or upstream-debug-signed APK:\n%s\n' \
  "$WORKDIR/launcher/app/build/outputs/apk/release/app-release.apk"
printf 'Install nothing automatically. Run Skeleton's approved signing, pairing and publish flow separately.\n'
