# Result handoff

Publish the result package to one immutable GitHub commit in a repository, branch, or Gist accessible to the operator.

The result location may be separate from alanua/Skeleton. Do not open a Skeleton PR and do not merge anything.

Your chat response must contain only:

```text
Repository: <URL>
Commit: <full SHA>
Summary:
- <line 1>
- <line 2>
- <up to line 5>
```

The commit must contain the complete package listed in TASK.md. Patches must apply to the exact base commit recorded in `manifest.json`. Do not paste implementation details, private data, logs, or long explanations into chat.
