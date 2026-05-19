# Skeleton GitHub Task Queue Runner

The GitHub task queue lets the Hetzner Runner poll open GitHub issues labeled `runner:ready`, extract a bounded Codex task from the issue body, run it in the Skeleton checkout, and report the result back to GitHub. When the task produces file changes, the runner validates them, commits them on `runner/issue-<number>`, pushes the branch, and opens a draft PR.

## Labels

Create these labels in the `alanua/Skeleton` repository:

```bash
gh label create runner:ready --repo alanua/Skeleton --description "Ready for Hetzner Runner pickup"
gh label create runner:done --repo alanua/Skeleton --description "Completed by Hetzner Runner"
gh label create runner:blocked --repo alanua/Skeleton --description "Blocked by Hetzner Runner"
```

## Task Issues

Create an issue and add the `runner:ready` label. Put the task inside a fenced block whose fence is three backtick characters followed by `task`:

````markdown
```task
Add the requested bounded change here.
Run the required validation and report the result.
```
````

Do not put secrets, API keys, environment files, production credentials, or private tokens in task issues.

## Hetzner systemd Setup

Install the service and timer on the Hetzner Runner host from the Skeleton repo checkout:

```bash
sudo cp scripts/skeleton-runner-poll.service /etc/systemd/system/
sudo cp scripts/skeleton-runner-poll.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable skeleton-runner-poll.timer
sudo systemctl start skeleton-runner-poll.timer
systemctl status skeleton-runner-poll.timer
journalctl -u skeleton-runner-poll.service -f
```

The service runs as user `agent` with `WorkingDirectory=/home/agent/agent-dev/repos/Skeleton`.

## Security

`gh auth` must already be configured on Hetzner for the `agent` user. The GitHub token must never be stored in this repo. API keys must never be put in task issues, comments, commits, docs, logs, or source files.

## Operation

The default script mode performs one poll pass and exits. The systemd timer starts that one-shot service every 60 seconds. The optional `--loop` flag is for manual debugging only.
