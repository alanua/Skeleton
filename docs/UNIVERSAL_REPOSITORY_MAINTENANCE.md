# Universal Repository Maintenance

The universal `repository_maintenance` executor accepts only typed payloads with
`schema=skeleton.runner_repository_maintenance_request.v1`.

Supported operations:

- `approved_pr_merge`: validates `alanua/Skeleton`, PR number, exact head SHA,
  reviewed file scope, `signed_telegram_callback` approval source, and squash
  merge intent. The default executor path reports the bounded merge command
  metadata without performing a live merge.
- `remote_read_only_diagnostic`: runs the executor-owned Home Edge
  `de_pc_read_only_v1` probe profile through `core.home_edge.action`. Receipts
  contain aggregate classes, counts, booleans, and stable reason codes only.

Unknown fields and non-allowlisted operations fail before side effects.
