# Home shared candidate review — #4928

Classification: REVIEW / PUBLIC_SAFE / SOURCE_ONLY.

Overlay against the verified private1.4.56 baseline plus startup/Games/stopped-artwork and updater fixes. Private source and confidential build properties are excluded; public main is an older release and must not become the release base.

Adds responsive remote geometry/48dp controls, visible-history counts, original-source icons, ordered bounded mode-scoped software remote requests, visible command errors, and generation checks against stale mode observations. Episode arrows, season persistence and canonical history stay preserved.

Apply helper/tests, then patch_android.py to updater-baseline MainActivity SHA2565fb6bf262430c2673f5ba7c3eb6716bc72ae55f8473adee8f91eca9a8e664d38, then patch_android_mode.py to SHA256bcc776cd37495d218c2b59668c26c01098ffd6eaf06f67842b3fec82ea163c47. Exact source boundaries fail closed. Final MainActivity SHA2562f48ce0fe2545bb4deeb364a103c79c73ea7d24dfeabcfa0fd513765d38a19ed.

Offline :app:testDebugUnitTest passed:71 tests, zero failures/errors/skips, one worker,300-second cap. Receipt4721a9bf52cdbaef6a3ca975587ad7233759a51a12615780f3286b7aee0c2843. Candidate source1.4.58/code174; release build receipt1974a944d3b0de4496842182e33647f82875988be472771fe3f2c2740ed97924.

Backend companion reviewed separately: expected_mode admission and source-page projection need activation for full behavior. JVM results are not phone UI/physical acceptance. #4928 R01–R28/U01 remains publication gate. No channel/installation change. Stable common172 retained; partial173 withdrawn. This PR is a source overlay review, not an instruction/canon change or a ready release.
