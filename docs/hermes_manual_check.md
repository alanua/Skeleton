# Hermes Manual Check

Status: public-safe manual dry-run command.

`scripts/hermes_check.py` is a small command-line wrapper around
`core.hermes_worker.run_hermes_worker_dry_run`. It is for checking one
public-safe Hermes test task by hand. It does not install Hermes, start a
service, run a background process, call the network, mutate GitHub, or write
files.

## What It Reads

By default, the command reads the bundled synthetic Hermes worker fixtures:

```bash
python3 scripts/hermes_check.py
```

An operator may provide an explicit public-safe task packet JSON file:

```bash
python3 scripts/hermes_check.py --packet path/to/public-safe-task-packet.json --no-skill
```

An operator may also provide an explicit public-safe skill manifest JSON file:

```bash
python3 scripts/hermes_check.py --packet path/to/public-safe-task-packet.json --skill path/to/public-safe-skill-manifest.json
```

The command should be used only with synthetic or otherwise public-safe JSON.
It is not a route for private customer data, secrets, private host paths, or
hidden operator text.

## What It Prints

The command prints a short public-safe result:

```text
HERMES_CHECK_RESULT=OK
DECISION=allowed
REASON=packet_satisfies_public_safe_dry_run_contract
WARNINGS=0
```

Possible `HERMES_CHECK_RESULT` values are:

- `OK`
- `REVIEW_REQUIRED`
- `OPERATOR_APPROVAL_REQUIRED`
- `BLOCKED`

The command does not print the input packet, the input path, private field
values, secrets, hidden text, or host paths. JSON read failures are reported as
`BLOCKED` with `REASON=input_json_invalid_or_unreadable`.

## Boundary

This command is advisory only. A passing manual check does not approve a skill,
activate Hermes, create a runtime, publish a branch, mutate an issue, or grant
operator approval. Any future Hermes runtime, service, install step, workflow
change, or live mutation still needs a separate reviewed approval path.

## Validation

Run the focused tests:

```bash
python3 -m pytest tests/test_hermes_check.py
```

Run the full suite before publishing a change:

```bash
python3 -m pytest
```
