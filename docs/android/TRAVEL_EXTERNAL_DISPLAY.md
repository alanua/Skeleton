# Opt-in S20 Travel external display

`AndroidTravelDisplay.open` is an explicit foreground Travel entry point for a
started ComponentActivity. It returns null outside Samsung base S20 SM-G980/981
models and rejects TV mode. S20 FE, Plus, Ultra and tablets are excluded. No Home
activity, TV/Tablet player, physical remote, pairing or network transport is changed.

An opted-in host owns one adapter and uses its `session` for output selection and
LOAD/PLAY/PAUSE/SEEK/STOP. Calls must run on the main thread. Select HDMI explicitly
before loading; this adapter renders external output only. PHONE loads fail closed.
The adapter observes host stop/destroy and closes permanently. Re-entry requires
fresh opt-in and a fresh session; it never resumes playback automatically.

The adapter discovers Android presentation displays and uses a Presentation,
SurfaceView and platform MediaPlayer. The first display by ID is selected; this
capability does not establish that the connection is wired HDMI. Display removal,
replacement, mode change, window dismissal or surface loss stops playback and
invalidates media. Surface creation prepares asynchronously; PLAY/PAUSE/SEEK during
preparation preserve intent. Decoder errors are sanitized through the travel
session and require a reload. STOP/close release the decoder and presentation.
No controls appear on the external display. No CEC or physical TV remote APIs are used.

Validation: `python3 -m unittest discover -s tests/android -p 'test_*.py'` checks
existing source contracts only. Android CI's existing `:app:testDebugUnitTest`
executes session tests and Robolectric gate/host lifecycle tests, followed by
`:app:assembleDebug`. The adapter is not wired into the default Home UI.

This issue environment has no JDK, Gradle or Android SDK, so Android compilation,
Robolectric execution and CI results remain unverified. No device, cable, HDMI,
codec, audio-route or physical remote behavior has been tested. A successful source
contract check is not evidence of physical HDMI playback.
