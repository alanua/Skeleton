# Runnable Task Issue Publishing

Runnable Runner task issues must be published through
`scripts/publish_task_issue.py`. The local markdown file is the source of truth;
the publisher validates it, creates or updates the GitHub issue with
`gh --body-file`, reads the issue body back, compares the remote body to the
local file exactly, and only then adds `runner:ready`.

Do not use inline `--body` transport for runnable task issues. Do not paste a
runnable task body directly into a shell command, chat prompt, Project draft
note, or other transport that can truncate or rewrite it before GitHub receives
it.

Project draft notes are not Runner tasks. A Runner task must be a linked GitHub
issue before it is added to a Project queue or board, and `runner:ready` belongs
on that linked GitHub issue only after publisher read-back verification passes.

## Body File Format

The file must be a local `.md` file. It must be non-empty, start with an opening
task fence, and end with the closing fence as the final line:

````markdown
```task
classification: YELLOW_LOCAL_PATCH
repo: alanua/Skeleton
intent: safe_task_issue_publisher
goal: describe the bounded result
scope:
- path/or/component
non_goals:
- excluded behavior
acceptance:
- observable success condition
```
````

Required sections:

- `classification`
- `repo`
- `intent`
- `goal`
- `scope`
- `non_goals`
- `acceptance`

The publisher fails closed when the body file is empty, the opening fence is
missing, the closing fence is missing from EOF, a required section is absent, or
the remote read-back body differs from the local file. On any validation or
read-back failure, the issue must not be labeled `runner:ready`.

## Commands

Create a new runnable task issue:

```bash
python3 scripts/publish_task_issue.py \
  --repo alanua/Skeleton \
  --title "Bounded Runner task title" \
  --body-file path/to/task.md
```

Update an existing runnable task issue:

```bash
python3 scripts/publish_task_issue.py \
  --repo alanua/Skeleton \
  --issue 861 \
  --body-file path/to/task.md
```

On success, the command prints JSON containing the issue number and URL. Treat
that output as the publication record for the Runner task.
