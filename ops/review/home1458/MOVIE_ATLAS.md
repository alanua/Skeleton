# Movie Atlas entry for the shared Home 1.4.58 candidate

REVIEW / SOURCE_ONLY / PUBLIC_SAFE. Issue #5010 continues #5009, #4928 and #5006. The Android overlay belongs to the single unpublished #4928 candidate. The controller owns draft publication and the full #4928 acceptance gate.

## Controller integration

1. Start with a separate copy of the reviewed Android project after the existing overlays. The default input SHA-256 for `MainActivity.kt` is the reviewed monitor output, `fdbfbd108bebeb5bd6a20c4ddd57d658f5ff3152f98db72f6ad4b731a06473a4`. For a newer reviewed source copy, supply its independently verified preimage using `--expected-sha256`. Review any boundary mismatch before revising the patch.
2. Copy `HomeMovieAtlas.kt` and `HomeMovieAtlasTest.kt` into the corresponding app source sets in that copy. Run `python3 ops/review/home1458/patch_movie_atlas.py <isolated-android-home-root>`. A second run with the same input SHA-256 must report `changed_files=0`. Record the resulting MainActivity SHA-256 there.
3. In the isolated project with the existing toolchain, run `gradle --offline :app:compileDebugKotlin :app:testDebugUnitTest --tests com.skeleton.home.HomeMovieAtlasTest`, then run the full candidate JVM suite offline. Actual-current-source compile and UI acceptance are controller gates.

## Launch contract

The Video screen's 48dp entry requests capabilities through the current `HomeApi` GET path with expected-server binding and redirects disabled for that GET. The gate requires the v1 schema, integer protocol 1, `supported=true`, `presentation_ready=true`, exactly `/movie-atlas`, and the selected endpoint in `supported_targets`. Missing, unauthorized, malformed and unsupported responses lead to the native unavailable state.

The native controller opens `/movie-atlas?endpoint_id=<encoded selected ID>` on the current server origin. System Back, the native back action and the same-origin `/movie-atlas/exit` navigation return to Video. Server, selected endpoint, screen deactivation and dismissal invalidate the launch. WebView teardown stops loading and destroys the view. Opening the controller issues no native presentation command.

Backend enablement remains separate for `alanua/skeleton-media#83` at `0133a0b0314d5802829876ecc7e9f20068c7703e`. Confirm the candidate's existing authorization reaches the capabilities GET and controller, that the backend consumes `endpoint_id`, and that the controller renders within the permitted top-level route. The exit path is intercepted locally. Verify that opening remains read-only for the receiver and that presentation requires the explicit “Показати на ТВ” action. UI acceptance also covers the selected Samsung or other endpoint, unavailable state, narrow and landscape layouts, and returning to the existing Video selection.

## DE-PC source and test receipt

Base `review/home-complete-candidate-4928` at `fc9f7a487f8a324954195c28dfbf2996e1e97846`. Local branch `codex/depc-5010-home-atlas-entry`, intended draft head branch `feat/home-movie-atlas-entry`. The five allowlisted additions are in the working tree. Publication and CI are pending controller review.

- `python3 -m pytest -q tests/test_home_movie_atlas_patch.py` passed all 10 synthetic cases with Python 3.12.3 and pytest 7.4.4.
- Adding `tests/android tests/test_home_android_media.py` produced 54 passes and 5 failures. A clean export of the exact base reproduced those same five public Android contract failures with 44 passes. They cover release metadata (two assertions), an updater primitive, the Video signature, and the history projection.
- Six executable Kotlin JVM tests are supplied. Java, Gradle and Kotlin were unavailable on the executor, so JVM execution and Android compilation remain pending. Patch application was limited to the synthetic fixture.

| File | SHA-256 |
| --- | --- |
| `HomeMovieAtlas.kt` | `9b8c0899ff2f57d280cf328f55e5ad8173c9c1405fa8f725b9b2988fbb95270c` |
| `HomeMovieAtlasTest.kt` | `186d9908f9688c4b12038cdf4530b5290b691fde765d36dcca141d3a625ee0e4` |
| `patch_movie_atlas.py` | `aced60d039f0967afa973811821f5c81aa6033910e181ff0cb880588f685724a` |
| `test_home_movie_atlas_patch.py` | `44ab1ff10662aed7e22c72e937167a18952b0b0109744f9e12ed32ccfb0e60dd` |
