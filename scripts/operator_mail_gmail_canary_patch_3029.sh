#!/usr/bin/env bash
set -euo pipefail

REPO='alanua/Skeleton'
BASE='c282c0608acebb994bfd4cffd5a5bea80cb67823'
BRANCH='runner/mail-gmail-canary-dispatch-direct-v1'
WT='/home/agent/agent-dev/worktrees/skeleton/issue-3029'

cd "$WT"
test -e .git || { echo "BLOCKED=issue_worktree_missing"; exit 1; }

echo "=== VERIFY EXACT MAIN ==="
git fetch --prune origin main
git fetch origin 'operator/mail-gmail-canary-patch-helper-v1' || true
MAIN="$(git rev-parse origin/main)"
echo "origin_main=$MAIN"
test "$MAIN" = "$BASE" || {
  echo "BLOCKED=main_moved"
  echo "expected=$BASE"
  echo "actual=$MAIN"
  exit 2
}

test -z "$(git status --porcelain)" || {
  echo "BLOCKED=worktree_not_clean"
  git status --short
  exit 3
}

ORIGIN_URL="$(git remote get-url origin)"
case "$ORIGIN_URL" in
  *alanua/Skeleton*|*alanua/Skeleton.git*) ;;
  *) echo "BLOCKED=wrong_origin:$ORIGIN_URL"; exit 4 ;;
esac

REMOTE_HEAD="$(git ls-remote --heads origin "refs/heads/$BRANCH" | awk '{print $1}')"
if [ -n "$REMOTE_HEAD" ] && [ "$REMOTE_HEAD" != "$BASE" ]; then
  echo "BLOCKED=remote_branch_already_has_changes"
  echo "remote_head=$REMOTE_HEAD"
  exit 5
fi

git switch -C "$BRANCH" "$BASE"
test "$(git rev-parse HEAD)" = "$BASE"

echo "=== APPLY ANCHOR-CHECKED PATCH ==="
python3 - <<'PY'
from pathlib import Path

runner_path = Path("scripts/runner_poll_github_tasks.py")
test_path = Path("tests/test_runner_poll_github_tasks.py")

src = runner_path.read_text()
tests = test_path.read_text()

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"BLOCKED=anchor_{label}_count_{count}")
    return text.replace(old, new, 1)

import_anchor = "from core.loop_state_store import LoopStateStore\n"
import_new = """from core.loop_state_store import LoopStateStore
from core.mail_gmail_production_canary import (
    GmailReadonlyCanaryError,
    MAIL_GMAIL_READONLY_CANARY_TASK_ID,
    allowed_gmail_canary_accounts,
    blocked_gmail_readonly_receipt,
    run_gmail_readonly_canary,
)
"""
src = replace_once(src, import_anchor, import_new, "gmail_import")

registry_anchor = """RUNTIME_MAINTENANCE_TASK_IDS = frozenset(
    (
        SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
"""
registry_new = """RUNTIME_MAINTENANCE_TASK_IDS = frozenset(
    (
        MAIL_GMAIL_READONLY_CANARY_TASK_ID,
        SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
"""
src = replace_once(src, registry_anchor, registry_new, "runtime_registry")

helper_anchor = "\ndef dispatch_runtime_maintenance_task(\n"

helper = r'''
_GMAIL_READONLY_CANARY_INPUT_FIELDS = frozenset(
    (
        "Mode",
        "Maintenance Task ID",
        "Repository",
        "Expected Main SHA",
        "Account Alias",
    )
)
_GMAIL_READONLY_CANARY_REQUIRED_FIELDS = _GMAIL_READONLY_CANARY_INPUT_FIELDS
_GMAIL_READONLY_CANARY_PUBLIC_RECEIPT_FIELDS = (
    "maintenance_task_id",
    "account_alias",
    "credential_binding_status",
    "oauth_refresh_status",
    "gmail_readonly_status",
    "probed_message_count",
    "mutation_attempted",
    "content_exposed",
    "stable_reason",
    "success_criteria",
)


def _gmail_readonly_canary_input(
    body: str,
) -> tuple[dict[str, str] | None, str | None]:
    metadata = (body or "").strip()
    if "```" in metadata:
        return None, "gmail_readonly_canary_fenced_input_not_allowed"

    parsed: dict[str, str] = {}
    for raw_line in metadata.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(
            r"(?P<field>[A-Za-z][A-Za-z0-9 ]*):\s*(?P<value>\S(?:.*\S)?)",
            line,
        )
        if match is None:
            return None, "gmail_readonly_canary_noncanonical_input"
        field = match.group("field")
        if field not in _GMAIL_READONLY_CANARY_INPUT_FIELDS:
            return None, "gmail_readonly_canary_unknown_input_field"
        if field in parsed:
            return None, "gmail_readonly_canary_duplicate_input_field"
        parsed[field] = match.group("value")

    if set(parsed) != _GMAIL_READONLY_CANARY_REQUIRED_FIELDS:
        return None, "gmail_readonly_canary_required_input_missing"
    if parsed["Mode"] != RUNTIME_MAINTENANCE_MODE:
        return None, "gmail_readonly_canary_mode_mismatch"
    if parsed["Maintenance Task ID"] != MAIL_GMAIL_READONLY_CANARY_TASK_ID:
        return None, "gmail_readonly_canary_task_id_mismatch"
    if parsed["Repository"] != REPO:
        return None, "gmail_readonly_canary_repository_mismatch"
    if _HEAD_SHA_RE.fullmatch(parsed["Expected Main SHA"]) is None:
        return None, "gmail_readonly_canary_expected_main_sha_invalid"
    return parsed, None


def _gmail_readonly_canary_receipt_report(
    receipt: Mapping[str, object],
    *,
    preflight_passed: bool,
) -> str:
    task_id = MAIL_GMAIL_READONLY_CANARY_TASK_ID
    success = receipt.get("success_criteria") == "met"
    count = receipt.get("probed_message_count")
    if (
        receipt.get("maintenance_task_id") != task_id
        or receipt.get("mutation_attempted") is not False
        or receipt.get("content_exposed") is not False
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count not in {0, 1}
    ):
        receipt = blocked_gmail_readonly_receipt(
            account_alias="UNREGISTERED",
            reason_code="GMAIL_READONLY_PROVIDER_FAILURE",
        )
        success = False

    status_lines = [
        "preflight_status=PASS" if preflight_passed else "preflight_status=NOT_RUN"
    ]
    if preflight_passed:
        status_lines.append("expected_main_sha_match=true")

    for key in _GMAIL_READONLY_CANARY_PUBLIC_RECEIPT_FIELDS:
        value = receipt.get(key)
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int) and not isinstance(value, bool):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = value
        else:
            continue
        status_lines.append(f"{key}={rendered}")

    return _maintenance_report(
        "DONE" if success else "BLOCKED",
        task_id,
        status_lines,
        "met" if success else "not_met",
    )


def _gmail_readonly_canary_preflight(
    expected_main_sha: str,
) -> str | None:
    task_id = MAIL_GMAIL_READONLY_CANARY_TASK_ID
    registered_checkout, status_lines, report = _mempalace_runtime_smoke_preflight(
        task_id
    )
    if report is not None or registered_checkout is None:
        return report or _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=registered_skeleton_checkout_unavailable"],
            "not_met",
        )

    checkout_path = registered_checkout.checkout_path

    head_sha, failure_report = _read_skeleton_sha(
        task_id,
        checkout_path,
        "HEAD",
        status_lines,
        "gmail_readonly_read_checkout_head",
    )
    if failure_report is not None or head_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=gmail_readonly_checkout_head_unavailable"],
            "not_met",
        )

    origin_main_sha, failure_report = _read_skeleton_sha(
        task_id,
        checkout_path,
        "origin/main",
        status_lines,
        "gmail_readonly_read_origin_main",
    )
    if failure_report is not None or origin_main_sha is None:
        return failure_report or _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=gmail_readonly_origin_main_unavailable"],
            "not_met",
        )

    expected = expected_main_sha.lower()
    if head_sha.lower() != expected or origin_main_sha.lower() != expected:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=gmail_readonly_expected_main_sha_mismatch"],
            "not_met",
        )
    return None


def mail_gmail_readonly_canary_v1(body: str) -> str:
    task_id = MAIL_GMAIL_READONLY_CANARY_TASK_ID
    parsed, reason = _gmail_readonly_canary_input(body)
    if reason is not None or parsed is None:
        return _maintenance_report(
            "BLOCKED",
            task_id,
            [f"reason={reason or 'gmail_readonly_canary_invalid_input'}"],
            "not_met",
        )

    account_alias = parsed["Account Alias"]
    if account_alias not in allowed_gmail_canary_accounts():
        return _gmail_readonly_canary_receipt_report(
            blocked_gmail_readonly_receipt(
                account_alias=account_alias,
                reason_code="GMAIL_ACCOUNT_ALIAS_NOT_ALLOWED",
            ),
            preflight_passed=False,
        )

    preflight_report = _gmail_readonly_canary_preflight(
        parsed["Expected Main SHA"]
    )
    if preflight_report is not None:
        return preflight_report

    try:
        receipt = run_gmail_readonly_canary(
            account_alias=account_alias,
            authority_environment=os.environ,
        )
    except GmailReadonlyCanaryError as exc:
        receipt = blocked_gmail_readonly_receipt(
            account_alias=account_alias,
            reason_code=exc.reason_code,
        )

    return _gmail_readonly_canary_receipt_report(
        receipt,
        preflight_passed=True,
    )


'''
src = replace_once(
    src,
    helper_anchor,
    "\n" + helper + "def dispatch_runtime_maintenance_task(\n",
    "gmail_helper",
)

dispatch_anchor = """    try:
        if task_id == BUILD_AND_LOCAL_OTA_OPERATION:
"""
dispatch_new = """    try:
        if task_id == MAIL_GMAIL_READONLY_CANARY_TASK_ID:
            return mail_gmail_readonly_canary_v1(body)
        if task_id == BUILD_AND_LOCAL_OTA_OPERATION:
"""
src = replace_once(src, dispatch_anchor, dispatch_new, "gmail_dispatch")

test_marker = "def test_gmail_readonly_canary_task_is_registered() -> None:"
if test_marker in tests:
    raise SystemExit("BLOCKED=gmail_tests_already_present")

test_block = r'''

def _gmail_readonly_canary_body(
    *,
    expected_main_sha: str = HEAD_SHA,
    account_alias: str = "acct:gmail-primary",
) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID}",
            f"Repository: {runner.REPO}",
            f"Expected Main SHA: {expected_main_sha}",
            f"Account Alias: {account_alias}",
        )
    )


def _gmail_readonly_success_receipt(
    account_alias: str,
    probed_message_count: int,
) -> dict[str, object]:
    return {
        "maintenance_task_id": runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID,
        "account_alias": account_alias,
        "credential_binding_status": "USED",
        "oauth_refresh_status": "PASS",
        "gmail_readonly_status": "PASS",
        "probed_message_count": probed_message_count,
        "mutation_attempted": False,
        "content_exposed": False,
        "stable_reason": "OK",
        "success_criteria": "met",
    }


def _gmail_readonly_preflight_patches(expected_main_sha: str):
    checkout = mock.Mock(
        checkout_path=Path("/synthetic/skeleton"),
        status_lines=[],
    )

    def read_sha(_task_id, _path, ref, _status_lines, _step):
        assert ref in {"HEAD", "origin/main"}
        return expected_main_sha, None

    return (
        mock.patch.object(
            runner,
            "_mempalace_runtime_smoke_preflight",
            return_value=(
                checkout,
                ["registered_checkout_current_main=true"],
                None,
            ),
        ),
        mock.patch.object(
            runner,
            "_read_skeleton_sha",
            side_effect=read_sha,
        ),
    )


def test_gmail_readonly_canary_task_is_registered() -> None:
    assert (
        runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID
        in runner.RUNTIME_MAINTENANCE_TASK_IDS
    )


def test_gmail_readonly_canary_rejects_near_miss_alias_before_preflight() -> None:
    with (
        mock.patch.object(
            runner,
            "_mempalace_runtime_smoke_preflight",
        ) as preflight,
        mock.patch.object(
            runner,
            "run_gmail_readonly_canary",
        ) as canary,
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID,
            "/synthetic",
            _gmail_readonly_canary_body(
                account_alias="acct:gmail-primary-near-miss"
            ),
        )

    assert runner.maintenance_report_status(report) == "BLOCKED"
    assert "preflight_status=NOT_RUN" in report
    assert "account_alias=UNREGISTERED" in report
    assert "stable_reason=GMAIL_ACCOUNT_ALIAS_NOT_ALLOWED" in report
    preflight.assert_not_called()
    canary.assert_not_called()


def test_gmail_readonly_canary_rejects_unknown_input_before_preflight() -> None:
    body = _gmail_readonly_canary_body() + "\nQuery: in:anywhere"
    with (
        mock.patch.object(
            runner,
            "_mempalace_runtime_smoke_preflight",
        ) as preflight,
        mock.patch.object(
            runner,
            "run_gmail_readonly_canary",
        ) as canary,
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID,
            "/synthetic",
            body,
        )

    assert runner.maintenance_report_status(report) == "BLOCKED"
    assert "reason=gmail_readonly_canary_unknown_input_field" in report
    preflight.assert_not_called()
    canary.assert_not_called()


def test_gmail_readonly_canary_blocks_expected_main_sha_mismatch_before_provider() -> None:
    checkout = mock.Mock(
        checkout_path=Path("/synthetic/skeleton"),
        status_lines=[],
    )
    with (
        mock.patch.object(
            runner,
            "_mempalace_runtime_smoke_preflight",
            return_value=(
                checkout,
                ["registered_checkout_current_main=true"],
                None,
            ),
        ),
        mock.patch.object(
            runner,
            "_read_skeleton_sha",
            side_effect=[
                ("b" * 40, None),
                ("b" * 40, None),
            ],
        ),
        mock.patch.object(
            runner,
            "run_gmail_readonly_canary",
        ) as canary,
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID,
            "/synthetic",
            _gmail_readonly_canary_body(
                expected_main_sha="a" * 40
            ),
        )

    assert runner.maintenance_report_status(report) == "BLOCKED"
    assert "reason=gmail_readonly_expected_main_sha_mismatch" in report
    canary.assert_not_called()


@pytest.mark.parametrize("probed_message_count", [0, 1])
def test_gmail_readonly_canary_success_is_bounded_and_invoked_once(
    probed_message_count: int,
) -> None:
    preflight_patch, sha_patch = _gmail_readonly_preflight_patches(HEAD_SHA)
    with (
        preflight_patch,
        sha_patch,
        mock.patch.object(
            runner,
            "run_gmail_readonly_canary",
            return_value=_gmail_readonly_success_receipt(
                "acct:gmail-primary",
                probed_message_count,
            ),
        ) as canary,
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID,
            "/synthetic",
            _gmail_readonly_canary_body(),
        )

    assert runner.maintenance_report_status(report) == "DONE"
    assert f"probed_message_count={probed_message_count}" in report
    assert "mutation_attempted=false" in report
    assert "content_exposed=false" in report
    assert canary.call_count == 1
    assert canary.call_args.kwargs["account_alias"] == "acct:gmail-primary"
    assert canary.call_args.kwargs["authority_environment"] is os.environ


def test_gmail_readonly_canary_maps_known_error_to_public_blocked_receipt() -> None:
    preflight_patch, sha_patch = _gmail_readonly_preflight_patches(HEAD_SHA)
    with (
        preflight_patch,
        sha_patch,
        mock.patch.object(
            runner,
            "run_gmail_readonly_canary",
            side_effect=runner.GmailReadonlyCanaryError(
                "GMAIL_CREDENTIAL_UNAVAILABLE"
            ),
        ) as canary,
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID,
            "/synthetic",
            _gmail_readonly_canary_body(),
        )

    assert runner.maintenance_report_status(report) == "BLOCKED"
    assert "stable_reason=GMAIL_CREDENTIAL_UNAVAILABLE" in report
    assert "mutation_attempted=false" in report
    assert "content_exposed=false" in report
    assert canary.call_count == 1


def test_gmail_readonly_canary_does_not_expose_unexpected_exception_text() -> None:
    sentinel = "PRIVATE_MAIL_SENTINEL_DO_NOT_EXPOSE"
    preflight_patch, sha_patch = _gmail_readonly_preflight_patches(HEAD_SHA)
    with (
        preflight_patch,
        sha_patch,
        mock.patch.object(
            runner,
            "run_gmail_readonly_canary",
            side_effect=RuntimeError(sentinel),
        ),
    ):
        report = runner.dispatch_runtime_maintenance_task(
            runner.MAIL_GMAIL_READONLY_CANARY_TASK_ID,
            "/synthetic",
            _gmail_readonly_canary_body(),
        )

    assert runner.maintenance_report_status(report) == "BLOCKED"
    assert "reason=maintenance_step_raised" in report
    assert sentinel not in report
'''

runner_path.write_text(src)
test_path.write_text(tests.rstrip() + "\n" + test_block.lstrip())

print("PATCH_APPLIED=1")
PY

echo "=== VERIFY FILE BOUNDARY ==="
CHANGED="$(git diff --name-only)"
printf '%s\n' "$CHANGED"

test -n "$CHANGED" || { echo "BLOCKED=no_diff"; exit 10; }

BAD="$(
  printf '%s\n' "$CHANGED" |
  grep -vE '^(scripts/runner_poll_github_tasks\.py|tests/test_runner_poll_github_tasks\.py|docs/MAIL_OPERATIONS\.md)$' ||
  true
)"
test -z "$BAD" || {
  echo "BLOCKED=changed_files_outside_allowlist"
  printf '%s\n' "$BAD"
  exit 11
}

printf '%s\n' "$CHANGED" | grep -qx 'scripts/runner_poll_github_tasks.py'
printf '%s\n' "$CHANGED" | grep -qx 'tests/test_runner_poll_github_tasks.py'

echo "=== STATIC VALIDATION ==="
python3 -m py_compile \
  scripts/runner_poll_github_tasks.py \
  tests/test_runner_poll_github_tasks.py
git diff --check

echo "=== FOCUSED TESTS ==="
python3 -m pytest -q \
  tests/test_runner_poll_github_tasks.py \
  -k 'gmail_readonly_canary'

echo "=== FULL TEST SUITE ==="
python3 -m pytest -q

echo "=== FINAL DIFF GUARD ==="
git diff --check
CHANGED="$(git diff --name-only)"
BAD="$(
  printf '%s\n' "$CHANGED" |
  grep -vE '^(scripts/runner_poll_github_tasks\.py|tests/test_runner_poll_github_tasks\.py|docs/MAIL_OPERATIONS\.md)$' ||
  true
)"
test -z "$BAD"

echo "=== COMMIT ==="
git add \
  scripts/runner_poll_github_tasks.py \
  tests/test_runner_poll_github_tasks.py

git diff --cached --check
git commit -m 'Register Gmail read-only canary dispatch'

HEAD_SHA="$(git rev-parse HEAD)"

echo "=== PUSH PROTECTED REVIEW BRANCH ==="
git push origin "HEAD:refs/heads/$BRANCH"

echo "=== RESULT ==="
echo "PATCH_HEAD=$HEAD_SHA"
echo "BASE=$BASE"
echo "BRANCH=$BRANCH"
echo "CHANGED_FILES:"
git diff --name-only "$BASE...HEAD"
echo "NO_MERGE=1"
echo "NO_RUNTIME_SYNC=1"
echo "NO_LIVE_GMAIL_CANARY=1"
