from __future__ import annotations

import json
from pathlib import Path

from scripts import runner_task_issue_linter as linter


VALID_BODY = """Expected Output: draft PR

```task
allowed_files:
  - scripts/runner_task_issue_linter.py
  - tests/test_runner_task_issue_linter.py
forbidden_actions:
  - do not run git add
  - do not run git commit
  - do not run git push
privacy_boundary: PUBLIC_SAFE_REPOSITORY_ONLY
idempotency_key: runner-task-issue-linter-v1
goal: add deterministic preflight linter
```
"""


def _codes(body: str) -> set[str]:
    return {finding.code for finding in linter.lint_issue_body(body)}


def test_valid_issue_body_passes() -> None:
    assert linter.lint_issue_body(VALID_BODY) == ()


def test_missing_task_block_is_reported() -> None:
    codes = _codes(
        "\n".join(
            (
                "allowed_files: README.md",
                "privacy_boundary: PUBLIC_SAFE_REPOSITORY_ONLY",
                "idempotency_key: no-task-block",
            )
        )
    )

    assert "MISSING_TASK_BLOCK" in codes


def test_malformed_allowed_files_is_reported() -> None:
    codes = _codes(
        VALID_BODY.replace(
            "allowed_files:\n  - scripts/runner_task_issue_linter.py\n  - tests/test_runner_task_issue_linter.py",
            "allowed_files:\n  scripts/runner_task_issue_linter.py",
        )
    )

    assert "MALFORMED_ALLOWED_FILES" in codes


def test_duplicate_idempotency_key_is_reported() -> None:
    codes = _codes(
        "Idempotency Key: runner-task-issue-linter-v1\n\n" + VALID_BODY
    )

    assert "DUPLICATE_IDEMPOTENCY_KEY" in codes


def test_conflicting_idempotency_keys_are_reported() -> None:
    codes = _codes("idempotency_key: other-key\n\n" + VALID_BODY)

    assert "CONFLICTING_IDEMPOTENCY_KEYS" in codes


def test_unsafe_wildcard_path_is_reported() -> None:
    codes = _codes(
        VALID_BODY.replace(
            "scripts/runner_task_issue_linter.py",
            "scripts/*.py",
        )
    )

    assert "UNSAFE_WILDCARD_PATH" in codes
    assert "MALFORMED_ALLOWED_FILES" in codes


def test_missing_privacy_boundary_is_reported() -> None:
    codes = _codes(
        VALID_BODY.replace("privacy_boundary: PUBLIC_SAFE_REPOSITORY_ONLY\n", "")
    )

    assert "MISSING_PRIVACY_BOUNDARY" in codes


def test_contradictory_forbidden_actions_are_reported() -> None:
    codes = _codes(
        VALID_BODY.replace(
            "  - do not run git push",
            "  - do not run git push\n  - allow git push",
        )
    )

    assert "CONTRADICTORY_FORBIDDEN_ACTIONS" in codes


def test_metadata_yaml_values_are_linted() -> None:
    body = """```yaml
allowed_files:
  - README.md
privacy_boundary: PUBLIC_SAFE_QUEUE_METADATA_ONLY
idempotency_key: metadata-yaml-task
forbidden_actions:
  - do not run gh pr create
```

```task
goal: metadata yaml feeds preflight lint
```
"""

    assert linter.lint_issue_body(body) == ()


def test_cli_json_reports_findings(tmp_path: Path, capsys) -> None:
    body_file = tmp_path / "task.md"
    body_file.write_text(VALID_BODY.replace("idempotency_key:", "idempotency key:"), encoding="utf-8")

    exit_code = linter.main([str(body_file), "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert {item["code"] for item in payload} == {"MISSING_IDEMPOTENCY_KEY"}
