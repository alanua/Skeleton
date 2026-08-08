# Home Edge YouTube Remote

`core.home_edge.youtube_remote` is the offline reference contract for a bounded
YouTube-focused phone remote. It does not connect to YouTube, Android TV, a
browser, SSH, ADB, MCP, or any Home Edge service.

## Closed Button Contract

The remote accepts only typed `YouTubeButton` values and `down`, `up`, or `tap`
phases. Every button has one fixed broker action under `home.youtube.*`; the
mapping is validated to exactly match the allowlist.

The button set is navigation, playback, captions, fullscreen, and mute:
`back`, D-pad directions, `ok`, `play_pause`, ten-second seek backward/forward,
previous, next, captions, fullscreen, and mute. Free-form text entry, search
queries, URLs, shell commands, device paths, and service names are outside the
contract.

## Progress-Context Safety Proof

Playback progress is optional evidence attached to progress-aware buttons. A
valid progress context must use an opaque `video_ref`, a positive duration, a
position inside the duration, and an observation timestamp that is not in the
future.

The broker emits a `ProgressSafetyProof` on every command:

- `fresh`: the context is at most 30 seconds old and may be used for local UI
  feedback only.
- `stale`: the context is preserved as evidence, but must not drive optimistic
  seek state.
- `absent`: no progress context is present or required.

The proof is explicit and bounded. The remote never derives control authority
from the progress snapshot and never sends live input.

## Validation

Run the offline tests with:

```bash
pytest tests/test_home_edge_youtube_remote.py
```

The tests verify allowlist and broker parity, fresh and stale progress proofs,
input rejection for URLs and invalid progress ranges, and absence of live device
control paths.
