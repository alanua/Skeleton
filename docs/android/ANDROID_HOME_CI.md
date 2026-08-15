# Android Home CI

`.github/workflows/android-home-test-build.yml` builds the native Android Home project for pull requests that touch `android/home/**`. It produces reviewable unsigned debug APK artifacts for operator testing only.

Security boundary:

- Runs on GitHub-hosted `ubuntu-24.04`.
- Uses a read-only `GITHUB_TOKEN` permission set: `contents: read`.
- Does not use repository or environment secrets.
- Does not sign, deploy, publish, release, or upload to Play Store.
- Does not use a self-hosted runner, `sudo`, root package installation, or Home/Home Edge/device/provider control.
- Operator dashboard validation is contract-only in CI. It verifies the installed APK has an app-owned `/api/operator/live-state` endpoint config and offline/stale behavior without contacting a live Home Edge node.
- Builds the exact pull request HEAD SHA. Manual `workflow_dispatch` has no ref input and is limited to current default-branch code.

The workflow uses JDK 17, Gradle 8.9, and only the Android SDK already present on the pinned GitHub-hosted image. It fails before build if `ANDROID_HOME`, `ANDROID_SDK_ROOT`, platform `android-35`, or build-tools `34.0.0` are unavailable.

Artifacts include only `app-debug.apk` and a public-safe metadata text file containing source SHA, APK SHA-256, byte size, and artifact scope. Stable distribution is intentionally out of scope; issue #2480 owns release/latest publishing after build proof.
