from __future__ import annotations

from pathlib import Path
import re


SCRIPT_PATH = Path("scripts/runner_poll_github_tasks.py")
TEST_PATH = Path("tests/test_runner_poll_github_tasks.py")


def replace_once(text: str, pattern: re.Pattern[str], replacement: str, label: str) -> str:
    updated, count = pattern.subn(lambda _match: replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return updated


def patch_script() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8")

    constants_pattern = re.compile(
        r"_MAINTENANCE_STATUS_TOKEN_RE = re\.compile\(\n.*?\n\)\n"
        r"_MAINTENANCE_PUBLIC_TEXT_BLOCK_MARKERS = frozenset\(\n.*?\n\)\n"
        r"_MAINTENANCE_PUBLIC_STATUS_KEYS = frozenset\(",
        re.DOTALL,
    )
    constants_replacement = '''_MAINTENANCE_ASSIGNMENT_KEY_RE = re.compile(
    r"^[a-z][a-z0-9_]{0,80}$"
)
_MAINTENANCE_SYMBOLIC_VALUE_RE = re.compile(
    r"^[A-Za-z0-9._:+,@/\\[\\]{}()#-]+$"
)
_MAINTENANCE_PR_URL_KEYS = frozenset(
    {"draft_pr_url", "existing_pr_url", "pr_url", "pull_request"}
)
_MAINTENANCE_FORBIDDEN_STATUS_KEYS = frozenset(
    {
        "checkout_path",
        "exception",
        "failed_command",
        "issue_worktree",
        "private_workspace",
        "raw_output",
        "stderr",
        "stdout",
        "traceback",
        "worktree_root",
    }
)
_MAINTENANCE_PUBLIC_STATUS_KEYS = frozenset('''
    text = replace_once(
        text,
        constants_pattern,
        constants_replacement,
        "maintenance constants",
    )

    functions_pattern = re.compile(
        r"def _sanitize_maintenance_status_lines\(status_lines: list\[str\]\) -> list\[str\]:\n"
        r".*?\n_HOST_INVENTORY_VALUE_RE =",
        re.DOTALL,
    )
    functions_replacement = '''def _sanitize_maintenance_status_lines(status_lines: list[str]) -> list[str]:
    sanitized: list[str] = []
    for line in status_lines:
        safe_line = _sanitize_maintenance_status_line(line)
        if safe_line is not None:
            sanitized.append(safe_line)
    return sanitized


def _maintenance_status_value_is_safe(key: str, value: str) -> bool:
    if (
        not value
        or "=" in value
        or _MAINTENANCE_SENSITIVE_VALUE_RE.search(value)
        or value.startswith(("/", "~"))
        or "\\\\" in value
        or ".." in value
    ):
        return False
    if key in _MAINTENANCE_PR_URL_KEYS:
        return _PUBLIC_GITHUB_PR_URL_RE.fullmatch(value) is not None
    if "://" in value:
        return False
    return _MAINTENANCE_SYMBOLIC_VALUE_RE.fullmatch(value) is not None


def _sanitize_maintenance_status_line(line: str) -> str | None:
    if "\\n" in line or "\\r" in line:
        return None
    tokens = line.strip().split()
    if not tokens:
        return None
    normalized: list[str] = []
    for token in tokens:
        if token.count("=") != 1:
            return None
        key, value = token.split("=", 1)
        if (
            _MAINTENANCE_ASSIGNMENT_KEY_RE.fullmatch(key) is None
            or key not in _MAINTENANCE_PUBLIC_STATUS_KEYS
            or key in _MAINTENANCE_FORBIDDEN_STATUS_KEYS
            or not _maintenance_status_value_is_safe(key, value)
        ):
            return None
        normalized.append(f"{key}={value}")
    return " ".join(normalized)


_HOST_INVENTORY_VALUE_RE ='''
    text = replace_once(
        text,
        functions_pattern,
        functions_replacement,
        "maintenance sanitizer functions",
    )

    SCRIPT_PATH.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    tests = TEST_PATH.read_text(encoding="utf-8")
    marker = "def test_maintenance_report_sanitizer_rejects_raw_blocks_paths_and_prose()"
    if marker in tests:
        raise RuntimeError("regression test already exists")

    for literal in (
        "failed_output_start",
        "failed_output_end",
        "remove_stderr_start",
        "remove_stderr_end",
        "AssertionError: private details",
    ):
        tests = tests.replace(
            f'assert "{literal}" in report',
            f'assert "{literal}" not in report',
        )

    tests += '''


def test_maintenance_report_sanitizer_rejects_raw_blocks_paths_and_prose() -> None:
    report = runner._maintenance_report(
        "BLOCKED",
        runner.VALIDATE_PR_BRANCH,
        [
            "failed_output_start",
            "AssertionError: private details",
            "failed_output_end",
            "remove_stderr_start",
            "arbitrary stderr text",
            "remove_stderr_end",
            "reason=arbitrary free text",
            "checkout_path=/home/agent/private",
            "pr_url=https://example.com/not-a-pr",
            "raw_output=plain_text",
            "stdout=plain_text",
            "stderr=plain_text",
            "failed_command=plain_text",
            "step=validation_profile_command_1 status=failed exit_code=1",
            "reason=maintenance_step_raised",
            "pr_url=https://github.com/alanua/Skeleton/pull/1168",
        ],
        "not_met",
    )

    for unsafe in (
        "failed_output_start",
        "AssertionError: private details",
        "failed_output_end",
        "remove_stderr_start",
        "arbitrary stderr text",
        "remove_stderr_end",
        "reason=arbitrary free text",
        "/home/agent/private",
        "https://example.com/not-a-pr",
        "raw_output=plain_text",
        "stdout=plain_text",
        "stderr=plain_text",
        "failed_command=plain_text",
    ):
        assert unsafe not in report
    assert "step=validation_profile_command_1 status=failed exit_code=1" in report
    assert "reason=maintenance_step_raised" in report
    assert "pr_url=https://github.com/alanua/Skeleton/pull/1168" in report
'''
    TEST_PATH.write_text(tests, encoding="utf-8")


def main() -> None:
    patch_script()
    patch_tests()


if __name__ == "__main__":
    main()
