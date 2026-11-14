# Web validation baseline audit

Date: 2026-11-14. Repository: `alanua/Skeleton`.
Initial and tested HEAD: `ad2b904fdf28e89893a33815682d5ad9a763a881` (the required main base; no drift).

The single bounded baseline completed with **27 failed, 1445 passed, 4 skipped**
(1476 total), exit **1**, in **252.51 seconds**. These failures reproduce on
the base without the candidate Page Publisher patch. This is baseline evidence,
not a green Web G1 or publication verdict, and not proof that candidates add no
other regressions.

## Reproduction and scope

Executed in the assigned issue worktree, using Python 3 and existing pytest
9.0.3, default serial execution (one worker), without dependency installation:

```bash
git rev-parse HEAD
git status --short
audit_tmp=$(mktemp -d /dev/shm/web-baseline-4991.XXXXXX) || exit 1
TMPDIR="$audit_tmp" PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  timeout 600s python3 -m pytest -q -p no:cacheprovider --tb=no \
  --junitxml="$audit_tmp/results.xml" \
  tests/android/test_home_foundation_contract.py \
  tests/test_home_android_media.py \
  tests/test_runner_child_environment_openrouter.py \
  tests/test_runner_poll.py \
  tests/test_runner_poll_github_tasks.py >"$audit_tmp/pytest.log" 2>&1
printf 'pytest_exit=%s\n' "$?"
tail -32 "$audit_tmp/pytest.log"
```

Only these five unchanged files were selected. Plugin autoload, bytecode writes
and pytest's repository cache were disabled. Existing skips were retained;
no skip/xfail or assertion changes were made. The run used synthetic tests in
the restricted environment; no live provider, credential, account or runtime
operation was requested. Test-created repositories and subprocess mocks are
test fixtures, not alternative task checkouts.

A preliminary launcher failed before pytest started: `/tmp` was unavailable
and `python` was not installed under that name. The command above was the only
actual pytest invocation. Generated XML/logs were directed to ephemeral
`/dev/shm/web-baseline-4991.zmD5cg`, outside public source. That directory was
not accessible from a subsequent tool invocation, so XML failure details were
not recovered. The completed command's short summary supplies exact failed IDs
and totals; assertion categories below are explicitly distinguished from
unavailable traceback evidence. No raw environment or stack locals are included.

## Candidate isolation and evidence limits

Initial tracked files were clean. Two pre-existing untracked Runner artifacts
were left untouched and not read. Only this audit document was authored.

Local Git object `b872424bf41bcf9e8df61f6995d31ac9b30abf62` is titled
`Publish issue #4982 worktree`. Its merge base with HEAD is exactly the tested
SHA. `git diff --name-status HEAD...b872424bf41bcf9e8df61f6995d31ac9b30abf62`
shows only:

```text
M core/page_pipeline.py
M core/page_publisher_backends.py
A tests/test_page_publication_postconditions.py
```

The added test file is absent at HEAD; the three-file candidate patch is not
in this worktree. A direct diff of the five selected tests between HEAD and
that object is empty. This independently supports the supplied description of
the #4988 Page Publisher change, but local commit metadata alone does not verify
the PR number or its current head.

Local ref/log searches did not identify #4978's G1 candidate or #4989/#4990
candidate-run evidence. No remote fetch, API lookup, other checkout or private
session artifact was used. HEAD and tracked content match the stipulated base;
the absence of #4978's specific six added files cannot be independently checked
without their names or candidate object. Their six-file/no-selected-test overlap
description is supplied task context, not a verified local diff.

## Sanitized failure categories

| Group | Failures | Evidence and limits |
| --- | ---: | --- |
| A: Android release metadata | 2 | Both failed tests explicitly require versionCode 155; base `android/home/app/build.gradle.kts:39` declares 160 (versionName 1.4.44). This source mismatch is directly verified. |
| B: Android header/signature | 1 | Failed header contract requires `VideoScreen(api:HomeApi,active:Boolean,selectionRevision:Int,endpointId:String`; current signature inserts `historyRevision:Int` before `selectionRevision:Int`. Verified source mismatch; the first runtime failing assertion was not captured. |
| C: Android history source contract | 1 | Failed history test requires literal `canonicalHistoryItems(api.get(mediaHistoryPath(endpointId)).optJSONArray` at line 83, absent in current MainActivity; several following history literals are also absent. Verified literal-contract mismatch, not a demonstrated functional history defect. |
| D: Runner credential binding source contract | 1 | Failed test forbids `bind_registered_environment_credential` anywhere in child-environment source; current source imports it and calls it in `prepare_primary_codex_environment` for the explicit API auth route. Verified source mismatch; this alone does not demonstrate implicit OpenRouter binding. |
| E: Runner primary-health mock contracts | 11 | Failed nodes exercise mocked verdicts, call counts, exact response and postcheck receipts. Source review shows those expectations. Exact observed assertion/verdict per node is unavailable; credential/health mock drift remains a hypothesis, not an established common cause. |
| F: Runner codegen/mock contracts | 11 | Failed nodes exercise mocked invocation/results, environment sanitation, manifests, secondary routing and timeouts. Runner source contains the `PRIMARY_API_CREDENTIAL_UNAVAILABLE` fail-closed path and calls `prepare_primary_codex_environment`; the short summary does not establish that this path caused any particular failure. |

Groups E/F describe the assertion surfaces in the unchanged tests, not recovered
exception messages. No failure is assigned to a credential cause just because
that category appeared in the task's candidate-run description.

## Exact failed node IDs

The parameter strings below are public synthetic fixture IDs, including the
literal `PRIVATE_MARKER`; they are not live provider output. Groups correspond
to the table above.

```text
A tests/android/test_home_foundation_contract.py::test_package_identity_label_compose_and_main_entry_are_current
B tests/android/test_home_foundation_contract.py::test_home_header_keeps_capture_and_hyperion_on_home_and_video
A tests/test_home_android_media.py::test_home_release_metadata_is_1_4_39_155
C tests/test_home_android_media.py::test_home_video_selection_and_history_are_canonical_endpoint_scoped
D tests/test_runner_child_environment_openrouter.py::test_child_environment_has_no_implicit_openrouter_binding
F tests/test_runner_poll.py::test_run_codex_task_calls_codex_exec
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_exact_registration_command_and_receipt
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_approval_framing_is_not_probe_authority
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_missing_readonly_help_makes_zero_provider_calls
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_classifies_failure_without_raw_output[Usage limit. Try again at 2026-09-22T12:34:56Z. PRIVATE_MARKER-CODEX_USAGE_LIMIT-2026-09-22T12:34:56Z]
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_classifies_failure_without_raw_output[Authentication failed PRIVATE_MARKER-CODEX_AUTH_FAILURE-none]
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_classifies_failure_without_raw_output[Service unavailable PRIVATE_MARKER-CODEX_PROVIDER_OUTAGE-none]
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_classifies_failure_without_raw_output[unclassified failure PRIVATE_MARKER-UNKNOWN_FAIL_CLOSED-none]
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_success_requires_exact_response
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_postcheck_detects_mutation[bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-True]
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_postcheck_detects_mutation[aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-False]
E tests/test_runner_poll_github_tasks.py::test_codex_primary_health_never_uses_fallback_or_codegen_state
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_sanitizes_home_edge_environment
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_uses_runner_owned_manifest_state_after_patch
F tests/test_runner_poll_github_tasks.py::test_codex_executor_sanitizes_parent_and_overlay_hmac_environment
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_explicitly_reroutes_quota_to_openhands
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_does_not_reroute_ordinary_failure
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_rejects_zero_edit_openhands_success
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_does_not_send_private_or_read_only_task_to_openhands
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_does_not_handoff_dirty_primary_worktree
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_bounds_primary_codex_invocation
F tests/test_runner_poll_github_tasks.py::test_run_codex_task_primary_timeout_fails_closed_with_retained_worktree
```

## Mapping and smallest next verification

| Candidate evidence | Mapping to this baseline |
| --- | --- |
| #4982 pre-publication run | Supplied context reports 27 failures in these same five unchanged files. Baseline count and selection agree; groups A–D have independent source mismatch evidence. The local #4982 source object confirms no selected-test changes. The candidate's actual failed node list/XML is missing, so exact set equality and E/F assertion correspondence remain unproven. |
| #4989 | Candidate IDs/results unavailable locally. No per-node correspondence established for any of the 27 failures. |
| #4990 | Candidate IDs/results unavailable locally. No per-node correspondence established for any of the 27 failures. |
| #4978 G1 / #4988 publication readiness | Existing baseline failures are demonstrated, but no candidate-versus-base regression verdict follows from equal totals. G1 source isolation and candidate-only tests still require verification against exact candidate objects. |

Smallest next step: obtain public-safe failed-node lists and sanitized assertion
summaries for #4982/#4989/#4990, plus exact candidate SHAs and the six #4978 paths,
then compare node sets and source diffs read-only. To establish E/F root causes,
separately authorize a focused offline diagnostic run that emits sanitized
assertion summaries in the same invocation before temporary artifacts disappear;
do not rerun providers or supply credentials. Subsequent candidate validation
must detect added failures, missing coverage and changes in failure reasons,
not merely compare counts. Source/test corrections require separate authorization;
none were made here.

Validation: `git diff --check` passed. Because this report is a new untracked
file, `git diff --no-index --check /dev/null docs/WEB_BASELINE_TEST_AUDIT.md`
also checked its contents with no whitespace diagnostics (exit 1 denotes the
added-file difference). Final HEAD remains the exact tested SHA.
