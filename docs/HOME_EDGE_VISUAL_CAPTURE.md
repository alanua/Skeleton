# Home Edge Visual Capture

`video_visual_capture` captures reviewed video evidence through one bounded private queue
tick. It is not generic browser automation and it is not a raw shell route.

## Contracts

- Job schema: `skeleton.home_edge.visual_capture.job.v1`
- Private manifest schema: `skeleton.home_edge.visual_capture.manifest.v1`
- Public receipt schema: `skeleton.home_edge.visual_capture.receipt.v1`

Public receipts contain only:

```text
schema
action_id
task_ref
status
frame_count
manifest_hash
capture_mode
reason_codes
retryable
human_review_required
stale
```

They never include URLs, video IDs, titles, cookies, browser profile paths, local artifact
paths, image bytes, raw manifests or arbitrary stdout/stderr.

## Runtime Configuration

Private runtime paths come only from fixed environment variables:

```text
SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_SPOOL
SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_ARTIFACT_ROOT
SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_BROWSER_PROFILE
SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_VISIBLE_KIOSK
SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_YTDLP
SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_FFMPEG
```

The spool root, artifact root and browser profile must resolve outside the public repository
and may not traverse symlinks. Files are written owner-only on POSIX hosts. Manifest writes
are atomic. Duplicate jobs return the existing public receipt.

## Capture Policy

Only normal YouTube watch URLs are accepted and normalized internally to
`https://www.youtube.com/watch?v=<id>`. The job cannot provide commands, selectors,
executable paths, output paths, hosts, users or ports.

The default capture mode is `background`. `visible_kiosk` is rejected unless explicitly
enabled by private runtime configuration. Default offsets are `[-3, -1, 0, 1, 3]`; offsets
must be integers within `-10..10`, with at most seven frames.

The private adapter should open media, seek, pause, wait for a stable decoded frame, hide
transient controls where possible and capture only the video/player region. It records
requested time, observed time, dimensions, SHA-256 values and a private contact sheet.
Timestamp drift beyond the bounded tolerance returns a stable recapture reason.

Consent, login, age-gate and cookie prompts return `INTERACTION_REQUIRED`. The adapter must
not click arbitrary prompts. The optional `yt-dlp` and `ffmpeg` fallback is fixed by private
runtime configuration only.

## Authority Boundary

Skeleton owns queue policy, audit metadata and sanitized receipts. Home Edge owns local
private browser/media execution. Screenshot and clip binaries remain in private artifact
storage outside the repository.

Canonical memory may later store only reviewed manifests, hashes and evidence state.
Graphify and MemPalace remain derived and non-authoritative.
