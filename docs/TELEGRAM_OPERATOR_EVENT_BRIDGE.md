# Telegram Operator Event Bridge

`core/telegram_operator_event_bridge.py` is the stage 1 dry-run bridge from a
Telegram approval callback to a public-safe GitHub-readable operator event. It
accepts a callback payload plus current pull request state supplied by the
caller, validates the callback through `core/telegram_approval_buttons.py`,
builds `core/operator_event.py` metadata, and renders deterministic issue
comment text without posting it.

Telegram is the operator console. Every operator callback must produce a
public-safe GitHub-readable event record. ChatGPT reconstructs operator state
from the GitHub audit trail, not chat memory.

The bridge binds the event record to the current repository, issue number, pull
request number, current head SHA, callback action, result, Telegram callback
source, public actor reference, UTC timestamp, and bounded summary. The bridge
returns a deterministic result with status, event dictionary, rendered issue
comment text, and public validation reasons. An `approve` callback can be
validated through the existing dry-run action gate; `reject`, `details`, and
`open_pr` callbacks render dry-run event records only.

Malformed callbacks, stale callback head SHAs, or callback repository and pull
request bindings that disagree with current state are rendered as blocked
events. Blocked rendering does not echo callback contents into the event
summary. Stage 1 comment rendering is public-safe and bounded: it carries
workflow metadata only, not secrets, source contents, private runner state, or
chat transcripts.

This stage renders comment text only. It does not call Telegram, write GitHub
issues, merge pull requests, deploy, access secrets, execute subprocesses, or
perform network work. A later live stage may post the rendered comment only
after its GitHub write behavior is reviewed and approved.
