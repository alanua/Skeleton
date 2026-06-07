# Runner Inbox

`tools/skeleton_core/runner_inbox.py` processes local Runner inbox packets for
public-safe Skeleton knowledge intake. Version 0 is intentionally narrow:

- allowed packet type: `append_review_queue_entries`
- allowed target: `projects/skeleton/REVIEW_QUEUE.yaml`
- allowed entry states: `REVIEW`, `BACKLOG`, `REJECTED`

Packets are local YAML mappings with `type`, `target`, and `entries`. The
processor validates the packet type, target path, exact entry fields, status
values, canon status, duplicate IDs, and secret-like text before writing. It
does not execute packet content, call network APIs, deploy, alter runtime
configuration, publish externally, or promote canon.

The write path is append-only for the review queue: existing file text and
entry ordering are preserved, and only rendered list items are appended under
`entries`. The processor validates the combined YAML before writing and parses
the file again after writing.

Example:

```yaml
type: append_review_queue_entries
target: projects/skeleton/REVIEW_QUEUE.yaml
entries:
  - id: RQ-2099-01-01-001
    source_batch: runner_inbox_example
    date: '2099-01-01'
    classification: REVIEW
    target_project: skeleton
    summary: Public-safe knowledge intake note.
    existing_match: No canon change.
    risk: Could be mistaken for canon if reviewed outside the queue.
    recommended_action: Keep in REVIEW until explicit operator approval.
    status: REVIEW
    canon_status: not_canon_until_promoted
```

Run locally:

```bash
python -m tools.skeleton_core.runner_inbox path/to/packet.yaml --repo-root .
```

The command prints a JSON report plus a compact human-readable report. Blocked
packets return exit code 2 and leave the review queue unchanged.
