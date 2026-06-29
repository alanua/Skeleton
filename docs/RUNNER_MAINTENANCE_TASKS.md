# Runner Maintenance Tasks

Runtime maintenance tasks are host Runner actions. They are not Codex tasks:
Codex stays inside its workspace sandbox and must not be asked to reach systemd
or host runtime paths.

The Runner accepts a runtime maintenance issue only when the issue is explicitly
operator-approved and declares the maintenance mode and allowlisted task id:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: sync_telegram_callback_poller_runtime
```

Issue text is not a shell script. The host Runner dispatches only task ids that
exist in its code allowlist and ignores any command-looking text in the issue.
Missing or unknown maintenance task ids are reported as `BLOCKED`.

## Current allowlist

`sync_telegram_callback_poller_runtime` may only:

1. Stop `skeleton-telegram-callback-poll.timer` and
   `skeleton-telegram-callback-poll.service`.
2. Update the Runner checkout from `origin/main`.
3. Verify the callback poller script and callback poller systemd unit files
   exist.
4. Copy only `skeleton-telegram-callback-poll.service` and
   `skeleton-telegram-callback-poll.timer` into `/etc/systemd/system`.
5. Set root ownership and `0644` permissions on those copied unit files.
6. Reload systemd, enable and start the callback timer, and run the callback
   service once.
7. Verify the callback timer is active and the one-shot callback service result
   is successful before reporting `DONE`.

Every privileged host command uses non-interactive `sudo -n`; the Runner must
block instead of waiting for operator input.

`ensure_telegram_callback_local_config` may only:

1. Create `/etc/skeleton-runner.env` if it is missing without reading config
   from issue text.
2. Set root ownership and `0600` permissions on that local environment file.
3. Add a generated `SKELETON_TG_CALLBACK_HMAC_SECRET` when that setting is
   missing or blank, or leave an existing nonblank setting unchanged.
4. Verify the callback HMAC setting exists before reporting `DONE`.

`private_memory_healthcheck` is a public-safe Runner boundary check for the
server-local private SQLite memory connector. It may only:

1. Read the private memory config location from the local Runner environment.
2. Call the `core.private_memory` connector.
3. Default to read-only healthcheck mode.
4. Report sanitized aggregate status fields only: configured/openable booleans,
   integrity and schema booleans, table count, heartbeat status, error class, and
   next-action token.
5. Fail closed as `BLOCKED` for missing config, invalid config, invalid registry,
   database open failure, integrity failure, schema mismatch, write failure, or
   any privacy violation.

It must not report raw config paths, database paths, registry paths, table names,
SQL text, row payloads, environment values, secrets, Drive identifiers, customer
data, room names, quantities, addresses, or file output.

Write-mode heartbeat is disabled by default. It is allowed only when the fenced
task payload explicitly requests it, for example:

````text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: private_memory_healthcheck

```task
heartbeat_write=true
```
````

This task proves the Runner can reach the private SQLite boundary. It does not
wire Hermes runtime, execute Aufmass, retrieve private task state, or enable live
provider/model routing.

`hermes_private_memory_bridge_check` is a public-safe aggregate bridge check for
the Hermes private-memory adapter. It requires no target metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: hermes_private_memory_bridge_check
```

It may only:

1. Call the Hermes private-memory bridge adapter functions.
2. Run the fixed sequence: read-only orient, blocked heartbeat write without an
   explicit gate, gated synthetic heartbeat, and gated synthetic note marker.
3. Report only aggregate status fields for that sequence:
   `hermes_bridge_status`, `orient_status`, `blocked_write_status`,
   `gated_heartbeat_status`, `gated_note_status`,
   `public_safe_report_ok`, `error_class`, and `next_operator_action`.
4. Fail closed as `BLOCKED` with the same aggregate field shape if any bridge
   function raises.

It must not report exception messages, raw config paths, database paths, table
names, SQL text, row payloads, environment values, secrets, tokens, Drive
identifiers, customer data, room names, quantities, addresses, or private memory
content. Bridge exceptions are summarized with safe tokens such as
`HermesBridgeException` and `safe_operator_review`; they must not fall through
to the generic maintenance-step exception report.

`install_graphify_runtime` is an approval-gated host runtime task for installing
the pinned Graphify assistant skills on the Runner host. It requires the exact
approval field:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: install_graphify_runtime
Operator Approval: install_graphify_runtime_v1
```

It may only:

1. Install or replace the pinned Graphify tool with
   `uv tool install --reinstall graphifyy==0.8.44`.
2. Verify the installed CLI contract before mutating assistant profiles:
   `graphify --version`, `graphify install --help`, and `graphify --help`.
3. Back up only the bounded Graphify-managed Codex and Hermes skill paths plus
   existing marker-only `.graphify_version` files discovered from the pinned
   Graphify 0.8.44 upstream platform destination allowlist, using a private
   `0700` recovery root and no symlink traversal.
4. Install skills with the Graphify 0.8.44 command forms:
   `graphify install --platform codex` and
   `graphify install --platform hermes`.
5. Run a temporary synthetic AST smoke using the supported build form
   `graphify <folder>` with `GRAPHIFY_OUT` pointed at a temporary output
   directory and a bounded timeout.
6. Verify the smoke output by reading `graph.json` and confirming non-zero node
   and edge counts.
7. Run that smoke with a scrubbed environment: no model credentials, no network
   enablement, no hooks, no services, no ports, and no private indexing.
8. Roll back the bounded Codex and Hermes skill paths and allowlisted
   `.graphify_version` files from the private recovery snapshot if either skill
   install, the synthetic smoke, or any unexpected runtime failure fails after
   the backup is taken.
9. Retain the private recovery snapshot after successful completion.

The marker allowlist is limited to user-level skill destinations that
Graphify 0.8.44 `_refresh_all_version_stamps()` can visit from
`safishamsi/graphify@5d053721aba875156cf2a6ddd6953d8beee98147`: aider, amp,
antigravity, antigravity-windows, claude, claw, codebuddy, codex, copilot,
devin, droid, hermes, kilo, kiro, kimi, opencode, pi, trae, trae-cn, and
windows. For claude/windows, the allowlist also covers the exact
`CLAUDE_CONFIG_DIR/skills/graphify/SKILL.md` destination when that environment
override is configured. It must never scan arbitrary home directories for
graphify-looking paths.

Runtime/server/service integration remains blocked by issue #1047; this task
only installs the local tool and approved assistant skills.

Command diagnostics are public-safe and stable. A missing `uv` executable reports
`graphify_tool_command_unavailable`; a missing `graphify` executable reports
`graphify_cli_command_unavailable`; permission failures report
`graphify_command_permission_denied`; bounded OS/subprocess launch failures
report `graphify_command_launch_failed`; and unexpected exceptions report
`graphify_runtime_unexpected_failure`. Failures before the recovery snapshot
report `rollback_status=not_needed`. Failures after the recovery snapshot report
`rollback_status=restored` or `rollback_status=failed`.

It must not run `graphify ingest`, `graphify install-skills`, `--source`,
`--extractor`, `--no-semantic`, live private indexing, hooks, services, network
providers, port listeners, or model/provider credentialed smoke tests. Reports
must remain aggregate-only and must not include profile paths, backup paths,
Graphify output, command output, environment values, secrets, tokens, model
credentials, profile content, node IDs, edge IDs, labels, summaries, or generated
graph payloads.

`check_project_checkout` is read-only and must include target project metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: check_project_checkout
Target Project: skeleton
```

It may only:

1. Resolve `Target Project` through `PROJECT_TREE.yaml`.
2. Verify the registered `checkout_path` has no `..` components and resolves
   under `/home/agent/agent-dev/`.
3. Check whether the checkout path exists.
4. Check whether `checkout_path/.git` exists.
5. If the checkout exists, run only
   `git -C {checkout_path} remote get-url origin`.
6. Compare that origin URL with the repository registered in
   `PROJECT_TREE.yaml`.

It reports `DONE` only when the checkout exists, `.git` exists, and origin
matches the registered repository. Missing target metadata, unknown projects,
unsafe paths, missing checkouts, missing `.git`, failed origin reads, and remote
mismatches are reported as `BLOCKED`.
Public reports include only `target_project`, `target_repository`, and
`target_project_route=registered_checkout` for the registered checkout route;
the registered checkout path remains internal to validation and bounded Git
commands.

`ensure_project_checkout` prepares only a missing registered project checkout and
must include target project metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: ensure_project_checkout
Target Project: skeleton
```

It may only:

1. Resolve `Target Project` through `PROJECT_TREE.yaml`.
2. Use only the registered repository and registered `checkout_path`.
3. Verify the registered `checkout_path` has no `..` components and resolves
   under `/home/agent/agent-dev/`.
4. If the checkout already exists, run the same `.git` and origin checks as
   `check_project_checkout` without preparing anything.
5. If the checkout is missing, run only
   `git clone https://github.com/{registered_repo}.git {registered_checkout_path}`.
6. After preparation, verify the checkout exists, `.git` exists, and origin
   matches the repository registered in `PROJECT_TREE.yaml`.

It reports `DONE` only when the checkout exists, `.git` exists, and origin
matches the registered repository. It reports `BLOCKED` for missing target
metadata, unknown projects, unsafe paths, path traversal, existing checkouts
without `.git`, wrong remotes, clone failures, failed origin reads, and remote
mismatches after preparation.
Public reports use the same registered checkout route fields as
`check_project_checkout` and do not include the registered checkout path.

`validate_pr_branch` validates an open pull request branch for an allowlisted
public repository and must include pull request metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: validate_pr_branch
Repository: alanua/Skeleton
Pull Request: 123
Expected Head SHA: 0123456789abcdef0123456789abcdef01234567
Validation Profile: full_pytest
```

`Repository` is optional and defaults to `alanua/Skeleton`; it may also be
supplied as `Target Repository`, `Selected Repository`, or `Repo`. `Pull
Request` is required. `Expected Head SHA` is optional but, when present, must
match the PR head reported by GitHub. `Validation Profile` is optional and
defaults to `full_pytest`; the only allowed values are `full_pytest`,
`knowledge_intake`, and `time_ledger_stage1`.

It may only:

1. Query PR metadata with `gh pr view --repo <allowlisted repository>`.
2. Continue only when the PR is open and targets base branch `main`.
3. Use the PR head SHA from GitHub metadata, not branch names or commands from
   issue text.
4. Prepare a dedicated validation worktree under the configured Runner
   worktree root for that repository at `validate-pr-branch/pr-{number}`.
5. Fetch only the GitHub PR head ref for the requested PR and verify it matches
   the exact PR head SHA.
6. Check out the validation worktree detached at the exact PR head SHA and
   verify `HEAD` before tests run.
7. Run only the selected allowlisted validation profile:
   `full_pytest` runs `python3 -m pytest -q`; `knowledge_intake` runs
   `python3 -m pytest -q tests/test_knowledge_intake.py` followed by
   `python3 -m pytest -q`; `time_ledger_stage1` runs
   `python3 -m pytest -q tests/test_time_ledger.py` followed by
   `python3 -m py_compile api/services/time_ledger.py
   api/services/arbzg_policy.py tests/test_time_ledger.py`.

It reports `DONE` only when PR metadata, safe workspace preparation, exact head
verification, and every profile command succeeds. Missing or invalid PR numbers,
unsupported repositories or profiles, closed PRs, non-`main` base branches,
expected head SHA mismatches, unsafe validation paths, fetch or checkout
failures, head mismatches, and test failures are reported as `BLOCKED`. Reports
include the exact repository, PR number, PR head branch, head SHA, allowlisted
commands, pass/fail status, and any detected missing dependency module names.
Failed validation profile commands include the allowlisted command and a
bounded, sanitized output block between `failed_output_start` and
`failed_output_end`; long output is truncated with an explicit marker.

`runtime_sync_main` synchronizes only the registered Skeleton checkout to the
configured `origin/main` by fast-forward. It requires no target metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: runtime_sync_main
Expected Head SHA: 0123456789abcdef0123456789abcdef01234567
```

`Expected Head SHA` is optional. When present, it must be a 40-character commit
SHA and must match both `origin/main` after fetch and final `HEAD`.

It may only:

1. Use the single registered Skeleton checkout from `PROJECT_TREE.yaml`.
2. Verify the checkout path is safe using the same path rules as
   `check_project_checkout`.
3. Verify the checkout exists, has `.git`, and has an `origin` remote matching
   `alanua/Skeleton`.
4. Require the active branch to be exactly `main`; detached HEAD and any other
   branch are blocked.
5. Require a clean checkout before fetching or fast-forwarding.
6. Run only bounded Git commands:
   `git -C {checkout_path} remote get-url origin`,
   `git -C {checkout_path} symbolic-ref --short HEAD`,
   `git -C {checkout_path} status --porcelain`,
   `git -C {checkout_path} fetch --prune origin main`,
   `git -C {checkout_path} rev-parse HEAD`,
   `git -C {checkout_path} rev-parse origin/main`,
   `git -C {checkout_path} merge-base --is-ancestor`, and
   `git -C {checkout_path} merge --ff-only origin/main`.
7. Fast-forward only when the registered checkout is behind `origin/main`.
8. Verify final `HEAD` matches `origin/main`, and the expected head SHA when
   supplied.

It reports `DONE` only when the checkout is already equal to `origin/main` or
was fast-forwarded to it. It reports `BLOCKED` for invalid expected SHA, unsafe
paths, missing checkouts, missing `.git`, wrong origin, detached HEAD, non-main
branches, dirty state, fetch failure, ahead or diverged state, fast-forward
failure, final head mismatch, and expected head mismatch. Reports must not
include absolute host paths or raw command output.
Public reports include only `target_project`, `target_repository`, and
`target_project_route=registered_checkout` for the registered Skeleton checkout;
the checkout path remains internal to validation and bounded Git commands.

`check_skeleton_freshness` is a short status-only check before Skeleton project
work starts or after recent merges. It requires no target metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: check_skeleton_freshness
```

It may only:

1. Use the registered Skeleton checkout from `PROJECT_TREE.yaml`.
2. Verify the checkout path is safe using the same path rules as
   `check_project_checkout`.
3. Run only bounded Git and GitHub status queries:
   `git -C {checkout_path} remote get-url origin`,
   `git -C {checkout_path} status --porcelain`,
   `git -C {checkout_path} fetch --prune origin main`,
   `git -C {checkout_path} rev-parse HEAD`,
   `git -C {checkout_path} rev-parse origin/main`,
   `git -C {checkout_path} ls-remote origin refs/heads/main`,
   `git -C {checkout_path} merge-base --is-ancestor`,
   `gh pr list --repo alanua/Skeleton --state open`, and
   `gh issue list --repo alanua/Skeleton --state open`.
4. Report whether GitHub `main` is the source of truth.
5. Report whether the live Runner checkout is equal to, ahead of, behind, or
   diverged from the current GitHub `main` SHA.
6. Report whether `docs/NOTEBOOKLM_SOURCEPACK.md` may need refresh when
   sourcepack or NotebookLM context is relevant.
7. Flag open PRs or issues that may need rebase, retest, or scope review against
   current `main`.
8. Remind that old chats and old branches are not canon.

It reports `DONE` when the checkout is clean and equal to, or safely ahead of,
current GitHub `main` and the freshness report was produced. It reports
`BLOCKED` for unsafe paths, missing checkouts, missing `.git`, failed origin
reads, dirty state, behind state, diverged state, failed GitHub `main` SHA
reads, GitHub query failures, or any unclassified sync state. The report must be
short, human-readable, and must not include raw command output or absolute host
paths. It may include only safe synthesized status lines such as current `main`
SHA, checkout sync state, open PR/issue counts, and bounded reminder notes.
Public reports use the same registered Skeleton checkout route fields as
`runtime_sync_main`; the checkout path remains internal to validation and
bounded Git commands.

`hermes_worker_preflight` is a read-only, report-only host inventory preflight
for the future Hermes worker. It requires no target metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: hermes_worker_preflight
```

It may only:

1. Collect bounded inventory with Python stdlib process inspection.
2. Hash the local host name before reporting any host identifier.
3. Report sanitized OS, kernel release, machine, Python version, Runner root
   existence, and basic tool presence for `python3`, `git`, `gh`, and `codex`.

It reports `DONE` when the inventory report is produced. This task must not run
shell commands, read environment values or secrets, mutate files, access
systemd, query GitHub, start Codex, or execute arbitrary issue text. Reports
must be short and include only sanitized key/value status lines; they must not
include raw command output, token values, raw host names, or issue-body text.

`hermes_memory_gateway_smoke` is a bounded public-safe contract smoke for the
Hermes Memory Gateway adapter. It requires no target metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: hermes_memory_gateway_smoke
```

It may only:

1. Use the synthetic namespace `aufmass` and a fixed synthetic project scope.
2. Create one in-memory Memory Gateway and reuse it for the full smoke.
3. Route Hermes memory operations only through `run_hermes_memory_task_packet`.
4. Verify exactly these six operations:
   `memory.lookup_exact`, `memory.get_conflicts`,
   `memory.get_override_history`, `memory.get_audit_log`,
   `memory.get_index_freshness`, and `memory.propose_patch`.
5. For each successful result, require the Hermes result schema, exact
   operation, namespace, project id, Gateway response schema, exact namespaced
   Gateway command, and Gateway contract version.
6. Require exact lookup payloads to be authoritative `canonical_exact` results
   from `canonical_sqlite` with a bounded canonical reference and integer
   canonical revision.
7. Require conflict, override-history, and audit summaries to contain bounded
   non-negative counts, and freshness summaries to report
   `freshness_checked=true`.
8. Require the first patch proposal to return
   `OPERATOR_APPROVAL_REQUIRED`, `canonical_write_requires_operator_approval`,
   and `NEW_PROPOSAL`; require an identical retry through the same Gateway to
   return `DUPLICATE_EXISTING`, `proposal_already_exists`, and duplicate
   classification.
9. Run exact lookup before proposal and after duplicate retry, and require the
   public exact summary, canonical reference, and canonical revision to remain
   unchanged as the no-canonical-write proof.
10. Require cross-project isolation to use the Hermes result schema,
    `status=BLOCKED`, and decision reason `PROJECT_NOT_AUTHORIZED`; require
    cross-namespace isolation to use the Hermes result schema, `status=BLOCKED`,
    and decision reason `NAMESPACE_NOT_AUTHORIZED`.

It reports `DONE` only when the full reviewed contract matches. Any envelope,
scope, command, payload, decision, isolation-reason, isolation status,
isolation schema, idempotency, or before/after-state mismatch is `BLOCKED` with
one stable sanitized failure token. The public report is aggregate only: it may
include the task id, operation count, contract version, and exactly one final
smoke status, but it must not print task packets, proposal content, canonical
values, event refs, paths, SQL, table names, environment values, secrets,
tokens, customer data, drawings, measurements, quantities, or raw exception
text.

`prepare_aufmass_private_runtime` verifies that the registered private Aufmass
runtime is ready for a controlled private pilot dry run. It requires no target
metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: prepare_aufmass_private_runtime
```

It may only:

1. Resolve the private Aufmass route through `PROJECT_TREE.yaml` by its fixed
   project id.
2. Verify that the route is registered as private and that its registered
   checkout and private workspace paths are absolute, traversal-free, under
   Runner-managed bases, and outside the public Skeleton checkout.
3. Check only bounded local inventory facts: private checkout exists, Git
   metadata exists, private workspace exists, the fixed private
   `source_pack_manifest.json` exists, the public pilot script exists, and
   `python3` is available.
4. Verify required public Python modules for the Aufmass pilot planner and the
   DXF parser dependency with fixed `python3 -c` import checks.
5. Run only the public pilot script in dry-run mode with the fixed private
   source-pack manifest and `manual-only` branch.
6. Verify that the pilot script returns the public-safe dry-run summary schema.

It reports `DONE` only when all registration, inventory, dependency, dry-run,
and public summary checks succeed. Missing private registration, unsafe
registered paths, missing private inventory, missing dependencies, dry-run
failures, malformed summaries, and non-dry-run summaries are reported as
`BLOCKED`. The task must not run arbitrary shell from issue text, install
packages, deploy, restart services, query GitHub for private paths, publish
private local paths, inspect drawings, calculate quantities, push branches,
create pull requests, or read secrets. Reports must include only safe status
booleans and step names, never private paths, drawing names, quantities, raw
command output, tokens, or issue-body text.

`run_aufmass_private_dxf_review` runs the controlled private Aufmass DXF review
pilot using only a private registry from the registered private Aufmass
workspace:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: run_aufmass_private_dxf_review
Private Source Pack ID: opaque-token
Pilot Mode: dry-run
```

`Pilot Mode` is optional and defaults to `dry-run`; the only allowed values are
`dry-run` and `execute`. GitHub issue text may provide only `Private Source Pack
ID` and optional mode metadata. It must not provide absolute paths, relative
paths, filenames, drawing names, room labels, quantities, dimensions, areas,
shell fragments, or registry contents.

It may only:

1. Resolve the private Aufmass route through `PROJECT_TREE.yaml` by its fixed
   private project id.
2. Read `automation_registry.private.json` from the registered private Aufmass
   workspace root.
3. Resolve the source-pack manifest, artifact map, output root, and optional run
   mapping from that registry using the opaque source-pack token.
4. Accept only registry paths that are relative to the private workspace,
   traversal-free, URL-free, and outside the public Skeleton repository.
5. Validate the resolved source-pack manifest with the existing source-pack
   validator before running the pilot.
6. For dry-run, run only
   `python3 -m scripts.aufmass_private_pilot_run --source-pack-manifest
   <resolved-private-manifest> --branch dxf-assisted`.
7. For execute, run only the same module invocation with `--execute`,
   `--private-workspace`, `--output-root`, and `--artifact-map`, all resolved
   from the private workspace and registry.

It reports `DONE` only when registry resolution, source-pack validation, the
bounded module invocation, and public-safe summary verification succeed. Public
GitHub output may include only status, maintenance task id, source pack token,
mode, branch, selected source count, DXF source count, artifact count, run token,
warning counts, and success criteria. It must not print private paths, registry
values, filenames, drawing names, source labels, room names, layers, dimensions,
areas, quantities, raw JSON, CSV contents, command output, secrets, or issue-body
text.

`summarize_aufmass_private_review` summarizes generated private review tables
without printing private row contents:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: summarize_aufmass_private_review
Private Source Pack ID: opaque-token
Run ID: optional-opaque-token
```

`Run ID` is optional; when omitted, the Runner uses the latest run registered
for that source-pack token. It may only resolve review artifacts through
`automation_registry.private.json` inside the registered private workspace. It
must summarize generated private review tables without printing paths, row
contents, labels, layers, room names, areas, dimensions, quantities, filenames,
raw JSON, or CSV contents.

It reports only row counts, review status counts, source token counts, warning
counts, status, source-pack token, run token, maintenance task id, and success
criteria. Missing or unsafe private registration, missing registry, unsupported
registry schema, unknown source pack/run tokens, unsafe registry paths, and
malformed review tables are handled with sanitized `BLOCKED` or warning count
status lines only.

`build_aufmass_private_shortlist` builds a smaller private operator review set
from existing private review table artifacts:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: build_aufmass_private_shortlist
Private Source Pack ID: opaque-token
Run ID: optional-opaque-token
```

`Run ID` is optional; when omitted, the Runner uses the latest run registered
for that source-pack token. It may only resolve input review tables and output
shortlist artifacts through `automation_registry.private.json` inside the
registered private Aufmass workspace. GitHub issue text may provide only the
opaque source-pack token and optional run token. It must not provide paths,
filenames, drawing names, room names, labels, layers, dimensions, areas,
quantities, row data, shell fragments, raw JSON, CSV contents, or registry
contents.

The task reads already generated private `*_room_review_table.json` artifacts
under the resolved run output root and writes private shortlist JSON and CSV
artifacts under that same output root. The private artifacts preserve row detail
needed for later manual review and include private filtering reasons. Rows with
usable room, label, and area evidence are preferred. The task does not calculate
final quantities.

The public GitHub report may include only status, maintenance task id,
source-pack token, run token, input table count, input row count, shortlist row
count, review status counts, warning count, and success criteria. It must not
print private paths, filenames, drawing names, room names, labels, layers,
dimensions, areas, quantities, raw JSON, CSV contents, command output, secrets,
or issue-body text.

`build_aufmass_private_area_schedule` builds the private payable area schedules
needed for Aufmass output from existing private review table artifacts:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: build_aufmass_private_area_schedule
Private Source Pack ID: opaque-token
Run ID: optional-opaque-token
```

`Run ID` is optional; when omitted, the Runner uses the latest run registered
for that source-pack token. It resolves private input and output only through
`automation_registry.private.json` inside the registered private Aufmass
workspace. GitHub issue text may provide only the opaque source-pack token and
optional run token. It must not provide paths, filenames, drawing names, room
names, labels, layers, dimensions, areas, quantities, raw rows, shell fragments,
raw JSON, CSV contents, or registry contents.

The task reads already generated private `*_room_review_table.json` artifacts
under the resolved run output root and writes private JSON plus room and wall
CSV schedule artifacts under that same output root. `room_area_schedule` rows
must contain private `room_ref`, explicit `floor_area_m2`, and
`ceiling_area_m2` fields; a generic `area_m2` field is promoted to private
`floor_area_m2` only when the source row has approved or strong review status.
When the ceiling area is copied from floor-area evidence by assumption, the
private evidence records that assumption in source, status, and confidence
fields. `wall_area_schedule` rows contain private `wall_ref`, `wall_length_m`,
`height_m`, `gross_wall_area_m2`, `opening_area_m2`, `opening_area_status`, and
`net_wall_area_m2`. A present zero opening area is marked `known_zero`; a
missing opening-area field is carried as `assumed_zero` with assumed evidence
confidence. One input row may not emit both a room and a wall quantity.

The task does not emit candidate, contour, fallback, weak, needs_review, or
area_mismatch rows as payable quantities and checks those weak row signals
before numeric parsing. It does not invent missing wall length, wall height,
floor, or ceiling quantities. If evidence is insufficient, it writes empty
private schedules with private diagnostic reasons. The public GitHub report may
include only status, maintenance task id, source-pack token, run token, room
area row count, wall area row count, warning count, diagnostic count, and
success criteria. It must not print private paths, filenames, drawing names,
room names, labels, layers, dimensions, areas, quantities, raw artifact content,
command output, secrets, or issue-body text.

`automation_registry.private.json` is private Runner state. Its contents may
contain private workspace-relative paths, but those values must never be
committed to the public repository, pasted into GitHub issues/comments, or
printed in Runner reports.

`inspect_issue_worktree_for_publish` is a Stage 1 delivery-only publisher
inspection. It is validation/dry-run only and must include explicit metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: inspect_issue_worktree_for_publish
Repository: alanua/Skeleton
Source Issue: 123
Expected Branch: runner/issue-123
Allowed Files:
- docs/RUNNER_MAINTENANCE_TASKS.md
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py
```

It may only:

1. Validate that `Repository`, when present, is `alanua/Skeleton`.
2. Validate `Source Issue` as a positive issue number.
3. Validate `Expected Branch` as exactly `runner/issue-{Source Issue}`.
4. Validate an explicit `Allowed Files` list of safe relative paths.
5. Resolve the issue worktree as `issue-{Source Issue}` under the configured
   Skeleton worktree root.
6. Verify the worktree exists, has Git metadata, and is on the expected branch.
7. Run only read-only Git status commands in that issue worktree:
   `git branch --show-current`, `git diff --name-only HEAD --`, and
   `git ls-files --others --exclude-standard`.
8. Ignore `.codex/` only as untracked runtime noise.
9. Report changed tracked files, unexpected untracked file count, and whether
   tracked files match the allowlist.

It reports `DONE` only when the inspection completes and all publish
preconditions are met. Unsupported repositories, invalid or missing metadata,
unsafe paths, missing worktrees, branch mismatches, command/read failures,
unexpected untracked files outside `.codex/`, and changed tracked files outside
the allowlist are reported as `BLOCKED`. This task must not push branches,
create pull requests, commit changes, merge, deploy, read secrets, or mutate
runtime services. Reports must not include raw command output, secrets, tokens,
environment values, or arbitrary task text.

`publish_issue_worktree_pr` is a Stage 2 issue-worktree draft PR publisher. It
is a host Runner runtime maintenance task, not a Codex responsibility, and must
include explicit metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: publish_issue_worktree_pr
Repository: alanua/Skeleton
Source Issue: 123
Expected Branch: runner/issue-123
PR Title: Optional safe title
Allowed Files:
- docs/RUNNER_MAINTENANCE_TASKS.md
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py
```

It may only:

1. Validate that `Repository` is exactly `alanua/Skeleton`.
2. Validate `Source Issue` as a positive issue number.
3. Validate `Expected Branch` as exactly `runner/issue-{Source Issue}`.
4. Validate an explicit `Allowed Files` list of safe relative paths using the
   same rules as `inspect_issue_worktree_for_publish`.
5. Resolve the issue workspace as `issue-{Source Issue}` under the configured
   Skeleton worktree root.
6. Verify the workspace exists, has Git metadata, is on the expected branch,
   and has origin set to the expected Skeleton GitHub remote.
7. Verify changed tracked files from `git diff --name-only HEAD --` are all
   inside `Allowed Files`.
8. Verify untracked files are absent except for `.codex/` runtime noise.
9. Query for an existing open PR for the expected branch and report `DONE` with
   that PR URL instead of creating a duplicate.
10. If allowed uncommitted tracked changes exist, run `git diff --check`, stage
    only those validated files, create one commit named
    `Publish issue #N worktree`, and verify branch `HEAD` moved.
11. If there are no uncommitted changes but the branch already differs from
    `main`, push and create the PR without creating another commit.
12. Push only `refs/heads/runner/issue-N:refs/heads/runner/issue-N` to origin.
13. Create a draft PR against `main` for the exact expected branch.

It reports `DONE` only when an existing PR is found or when the exact branch
push and draft PR creation succeed. Unsupported repositories, missing or
invalid metadata, unsafe paths, missing workspaces, missing Git metadata, branch
mismatches, remote mismatches, no publishable changes, changed files outside the
allowlist, unexpected untracked files, diff-check failures, staging failures,
commit failures, branch `HEAD` verification failures, GitHub access failures,
push failures, and PR creation failures are reported as `BLOCKED`. This task
must not force-push, merge, deploy, read secrets, mutate runtime services, use
issue-provided paths, or execute arbitrary issue text. Its only allowed commit
is the single validated issue-worktree publish commit described above.

`publish_existing_issue_worktree` is the bounded recovery route for publishing
work that already exists in a Runner issue worktree. Use it when normal issue
mode would allocate a new worktree but the operator needs to recover a specific
existing `issue-N` worktree under the known Runner worktree base.

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: publish_existing_issue_worktree
Target Repository: alanua/Skeleton
Source Issue: 822
Base Branch: main
Output Branch: runner/issue-822
Draft PR: true
Allowed Files:
- path/explicitly/allowed.ext
```

When the GitHub existing-PR lookup is unavailable, this route remains
fail-closed unless the issue also contains an explicit structured override:

```text
Publish Override: {"action":"publish_existing_issue_worktree","allowed_files":["path/explicitly/allowed.ext"],"base_branch":"main","draft_pr":true,"output_branch":"runner/issue-822","source_issue":822,"target_repository":"alanua/Skeleton"}
```

The override must exactly match the maintenance action, target repository,
source issue, output branch, base branch, draft flag, and explicit allowlisted
file set from the same issue. It only authorizes staging/committing/pushing
those files and creating a draft PR after the runner verifies that the output
branch is absent remotely. It never authorizes merge, deploy, runtime
activation, canon promotion, secrets, destructive cleanup, force-push, broad
`git add`, or issue-provided commands. Runner labels, task completion text,
generic chat approvals such as `+`, model output, and other issues are not
approval sources.

It may only:

1. Validate `Target Repository` as exactly `alanua/Skeleton`.
2. Validate `Source Issue` as a positive issue number.
3. Validate `Base Branch` as `main`.
4. Validate `Output Branch` as exactly `runner/issue-{Source Issue}`.
5. Validate `Draft PR: true`; this route never creates a ready-for-review PR.
6. Validate an explicit `Allowed Files` list of safe relative paths.
7. Resolve the source worktree as `issue-{Source Issue}` under the configured
   Skeleton Runner worktree root and reject paths outside that root.
8. Verify Git metadata, current branch, and origin remote before reading diffs.
9. Verify changed tracked files are a subset of the explicit allowlist.
10. Ignore only local `.codex/` untracked runtime artifacts; never stage them.
11. Reuse an existing open PR when lookup succeeds with a match.
12. Treat a successful empty lookup as normal new draft-PR publication.
13. If lookup is unavailable, require an exact structured override and verify
    the output branch is absent remotely before publishing.
14. Stage only validated publish files, push only the exact output branch, and
    create only a draft PR against the base branch.
15. Never merge, force-push, deploy, restart services, read secrets, execute
    issue-provided commands, or use broad `git add`.

It reports `DONE` only when an existing open PR is found or when the exact
branch push and draft PR creation succeed. Operator-action failures are reported
as `NEEDS_OPERATOR` with sanitized key/value status lines only; raw command
output, registered checkout paths, absolute host paths, private paths outside
the Runner worktree contract, secrets, task text, and quoted transcripts must
not be included. Public reports may retain target identity with symbolic fields
such as `target_project`, `repository`, and `issue_worktree_id`.

Stable lookup and override reason tokens are:

- `existing_pr_found`
- `existing_pr_not_found`
- `existing_pr_lookup_unavailable`
- `publish_override_missing`
- `publish_override_malformed`
- `publish_override_scope_mismatch`
- `remote_branch_conflict`
- `publish_override_valid`

`publish_target_project_issue_worktree_pr` is the bounded cross-project
publisher for work that already exists in a registered public target-project
issue worktree. It resolves the target repository and worktree root only through
`PROJECT_TREE.yaml`; issue bodies must not provide source paths.

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: publish_target_project_issue_worktree_pr
Target Project: lumenflow
Target Repository: alanua/LumenFlow
Source Issue: 1004
Base Branch: main
Output Branch: runner/issue-1004
Draft PR: true
Allowed Files:
- README.md
- docs/RUN_LINUX_MINT.md
- deploy/home-runner/README.md
- deploy/home-runner/app.yaml
- deploy/home-runner/install.sh
- deploy/home-runner/start.sh
- deploy/home-runner/status.sh
- deploy/home-runner/stop.sh
- deploy/home-runner/rollback.sh
```

It may only:

1. Validate `Target Project` and `Target Repository` against the same
   public, `runner_enabled` `PROJECT_TREE.yaml` entry.
2. Derive the source worktree as
   `<registered target_project worktree_root>/issue-{Source Issue}`.
3. Reject issue-provided source paths, absolute paths, traversal, mismatched
   project/repository metadata, private repositories, and disabled projects.
4. Validate current branch, origin remote, base branch, output branch, draft
   status, changed tracked files, and allowed untracked files before publishing.
5. Ignore only local `.codex/` untracked runtime artifacts.
6. Run `git diff --check`, stage only validated files, commit with the
   deterministic target-project publish message, push only the exact expected
   branch ref, and create only a draft PR against `main`.
7. Reuse an existing open PR for the same head branch instead of creating a
   duplicate.
8. Never merge, force-push, deploy, restart services, read secrets, execute
   issue-provided commands, use broad `git add`, or support private repositories.

It reports only public-safe route identity such as `target_project`,
`repository`, `target_project_route`, and `issue_worktree_id`. The registered
checkout path, resolved worktree root, and absolute source worktree path remain
internal validation and Git-command inputs and must not be printed in `DONE`,
`BLOCKED`, or `NEEDS_OPERATOR` reports.

`quarantine_stale_clean_skeleton_worktrees` removes only explicitly listed
clean Skeleton issue worktrees and must include explicit worktree id metadata:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: quarantine_stale_clean_skeleton_worktrees
Repository: alanua/Skeleton
Issue Worktrees:
- issue-123
- issue-124
Protected IDs:
- issue-834
```

`Repository` is optional but, when present, must be exactly `alanua/Skeleton`.
`Protected IDs` is optional. Worktree ids must be literal `issue-N` names, not
paths.

It may only:

1. Resolve each listed id as `issue-N` under
   `/home/agent/agent-dev/worktrees/skeleton`.
2. Skip protected ids before running any Git commands for them.
3. Skip missing paths, missing Git metadata, unsafe paths, dirty worktrees, and
   worktrees whose origin remote is not `alanua/Skeleton`.
4. For existing non-protected candidates, run only
   `git remote get-url origin` and `git status --porcelain` inside the
   candidate worktree before removal.
5. Remove only candidates that exist, have Git metadata, match the Skeleton
   remote, and have empty porcelain status, using
   `git worktree remove {resolved_issue_worktree_path}`.

It reports `DONE` when the listed candidates have been inspected and every
eligible clean Skeleton issue worktree was removed. Missing, dirty, wrong
remote, missing Git metadata, unsafe, and protected candidates are reported as
skipped. Unsupported repositories, missing or invalid worktree id metadata,
duplicate ids, invalid protected ids, unsafe configured roots, command failures
while removing an eligible worktree, and unexpected handler errors are reported
as `BLOCKED`. This task must not delete arbitrary paths, use shell commands,
push, merge, deploy, read secrets, mutate runtime services, or execute arbitrary
issue text.

The allowlist does not permit rebooting the host, package upgrades, arbitrary
commands or config values from issue text, or unrelated services.

## Reporting

Each maintenance report must state `DONE` or `BLOCKED` accurately with safe
status lines only. A failed maintenance step or failed runtime verification is
`BLOCKED`, and a report that contains `BLOCKED` or `success_criteria=not_met`
must not receive the `runner:done` label.

Reports must not print token values or raw command output.
