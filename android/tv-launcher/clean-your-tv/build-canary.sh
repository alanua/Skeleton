#!/usr/bin/env bash
# Reconstruct the vetted Clean Your TV native Skeleton Apple dock.
# Source-only. Never connects to a TV or invokes upstream build-and-install.sh.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HERE="$REPO_ROOT/android/tv-launcher/clean-your-tv"
PIN="bfe73278774116f887d77de60b782e726ba149d5"
DEST="${1:?usage: build-canary.sh /path/to/empty/output-directory}"
test ! -e "$DEST" || { echo "Refusing nonempty destination" >&2; exit 1; }
git clone --no-checkout https://github.com/Gathin23/cleanyourtv.git "$DEST"
git -C "$DEST" checkout --detach "$PIN"
git -C "$DEST" apply "$HERE/apple-dock.patch"
cp "$HERE/assets/banner_skeleton_video.xml" "$DEST/launcher/app/src/main/res/drawable/"
cp "$HERE/assets/banner_skeleton_tv.xml" "$DEST/launcher/app/src/main/res/drawable/"
echo "Upstream MIT license retained. TV not changed."
echo "Build manually in $DEST/launcher with Gradle testDebugUnitTest assembleRelease."
