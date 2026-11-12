# Home release evidence review

This small wrapper reviews pre-produced, sanitized reports from Git comparison or
bisect, diffoscope, JADX, Androguard, REA/Ghidra and Android/device checks. It does
not run those tools, inspect APKs, install software or control devices.

The input schema is `schemas/home_media_regression_guard.v1.schema.json`.
Run `python core/home_media_regression_guard.py manifest.json`; exit 0 means the
supplied evidence is complete, exit 2 means blocked or invalid input. JSON Schema
checks structure; the evaluator additionally checks artifact binding, unique
identities, timestamp validity and the mandatory feature/evidence floor.

`READY_FOR_RELEASE` describes supplied evidence only. `authorization: none`
always remains in the output. Actual report authenticity, source provenance,
operator review and existing Skeleton merge/sign/promotion/install approvals
remain necessary. A caller can supply fabricated reports; this wrapper cannot
authenticate an execution receipt or verify an APK signature from JSON alone.

## Artifact identity

Use the existing release field names `version_name`, `version_code`, `sha256`
(APK) and `signer_sha256` (signing certificate fingerprint). Keep private
`apk_path`, endpoint settings and signer material out of this manifest.
`source_sha256` identifies the exact reviewed source snapshot using a documented
snapshot procedure; a single-file digest proves only that file. Preserve an
unknown `source_commit` as null instead of deriving ancestry from a folder name.
Report digests are SHA256 of the retained report artifact. All feature evidence
must identify the candidate source AND candidate APK; another artifact cannot
satisfy the review. A new APK requires a greater versionCode and the same signer.
Debug/preview builds cannot satisfy production readiness.

An unknown source/APK/signer digest deliberately blocks release readiness. Thus
a successful Kotlin unit-test compile with no APK remains useful test evidence,
but it cannot produce release-ready output. No existing source or release is
rewritten to make the report pass.

## Feature continuity

Carry every baseline feature into the candidate report. The minimum checklist
covers cold/warm Home startup, endpoint selection, mode routing, incoming phone
cast, stopped/playing/paused poster identity, episode arrows, season persistence,
history membership and hidden works, source links/favicon, Samsung separation,
Secrets and updater/signing lineage. The evaluator retains this floor even if
an input attempts to mark a built-in feature optional or lower its evidence level.

Results are `pass`, `fail`, `unverified` or `not_applicable`, with a reason and
artifact-bound evidence. Source and unit checks cannot substitute for Android UI
or real-device evidence. In particular an incoming cast and Samsung separation
require device evidence; opening a cast target or seeing a warm Chrome process
does not prove a newly playing phone-origin video. New additional features must
be explicitly added to the baseline review scope. Optional features still need
an explicit candidate entry, and failures remain visible as warnings.

## Tool inventory and privacy

The November 3 DE-PC/WSL inventory in Skeleton #4939 recorded REA 6.8.0,
Ghidra 12.1.4, diffoscope 336, Androguard 4.1.4 and JADX 1.5.6. That is historical
evidence, not verification that an offline workstation is currently usable.
BinDiff/Diaphora were candidates. Avoid duplicate installations.

Tool states distinguish `historically_verified`, `currently_verified`,
`unavailable` and `not_required`. Current verification requires version,
provenance, verification time and an explicit validity deadline. A baseline
`required_tools` entry cannot be removed by marking a tool optional in the
candidate inventory. Optional unnecessary binary tools may be recorded with a
reason without blocking a source task. No arbitrary freshness interval is
invented; the reviewed inventory provides its validity deadline.

Inputs must be sanitized before review. The closed schema rejects unexpected
fields; the output does not echo reason text, paths or raw parser errors. This
is not a secret scanner: never submit credentials or private URLs in any field.
Keep raw APK analysis, local paths and private configuration in private storage.

## Existing authorities and scope

Source reference: Skeleton main `ad2b904fdf28e89893a33815682d5ad9a763a881`.
The existing `.github/workflows/android-home-test-build.yml` binds debug artifacts
to the exact PR head and records APK hash/size. Existing
`skeleton_media/cast/native_app_update_manifest.py` in alanua/skeleton-media
verifies published aliases and release metadata. This wrapper consumes evidence
from those authorities; it creates no second updater or publication mechanism.

Validation uses only synthetic manifests and CLI inputs in temporary test
directories. Real DE-PC inventory, binary release comparison, receiver-event
repair, Android UI tests and device acceptance remain separate work. The guard
is not integrated into a release pipeline by this source-only patch.
