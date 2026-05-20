# Runner Bridge

The current live execution route remains the GitHub issue queue processed by
`scripts/runner_poll_github_tasks.py`. That poller looks for GitHub issues with
the runner labels and extracts a fenced `task` block from the issue body.

`core/runner_bridge.py` is stage 1 only: a local deterministic dry-run contract.
It validates a requested runner task and renders the GitHub issue body that
would be submitted to the current queue route. It does not create issues, call
GitHub, call network APIs, invoke systemd, run Codex, start shells, or execute
subprocesses.

Validation commands are an allowlist, not general shell commands. Stage 1
accepts only these exact command strings:

- `python3 -m pytest -q`
- `python -m pytest -q`
- `pytest -q`
- `git diff --check`
- `git status --short`

All other commands are blocked, including git commands that can mutate local or
remote state.

A dry-run result must not be promoted automatically to live GitHub issue
creation. Any future dry-run to live promotion requires a separate
operator-approved stage 2 PR with explicit review of the live side effects. It
must not be implemented as a flag flip on the stage 1 dry-run path.
