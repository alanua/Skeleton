# Home APK Release URL Gate

Home APK `1.4.37` / `versionCode 153` repairs the production release configuration
regression from `1.4.36` / `versionCode 152`, where a production APK could be
assembled with an empty `BuildConfig.HOME_EDGE_BASE_URLS` value.

## Release Build Guard

`android/home/app/build.gradle.kts` requires `-PhomeEdgeBaseUrls=...` whenever
Gradle is asked to run `assembleRelease`. The value must contain at least one
comma-separated HTTP or HTTPS base URL. Blank entries are ignored, and every
remaining entry must parse as a URL with a host.

The release guard intentionally reports only a generic configuration error. It
must never print, log, document, or hardcode private production endpoints.

Example shape:

```bash
./gradlew :app:assembleRelease -PhomeEdgeBaseUrls=https://example.invalid
```

Use a real production endpoint only in the private release environment. Do not
commit it to source control, tests, docs, or issue comments.

## Production Verification

Before signing or publishing a production APK, verify the following in the
private release environment:

- `assembleRelease` fails when `homeEdgeBaseUrls` is omitted.
- `assembleRelease` fails when `homeEdgeBaseUrls` is blank.
- `assembleRelease` fails when any nonblank entry is not an HTTP or HTTPS URL
  with a host.
- `assembleRelease` succeeds with the approved production URL list supplied via
  Gradle property.
- The generated release `BuildConfig.HOME_EDGE_BASE_URLS` is nonempty.
- No private endpoint appears in Gradle output, repository files, tests, docs,
  or release notes.

This change does not install, play media on, sign, or publish an APK.
