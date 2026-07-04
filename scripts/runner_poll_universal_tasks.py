#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from core.governed_runtime import execute_governed_envelope_file
from core.runtime_context_loader import load_runtime_context
from core.task_envelope_queue import (
    QueueEnvelopeRequest,
    TaskEnvelopeQueueError,
    parse_queue_request,
)
from core.task_reference import (
    TaskEnvelopeReference,
    resolve_task_envelope_file,
)
from scripts import runner_poll_github_tasks as legacy_runner

ENVELOPE_INBOX_ENV = "SKELETON_RUNNER_ENVELOPE_INBOX"
EVIDENCE_DIR_ENV = "SKELETON_RUNNER_EVIDENCE_DIR"
IDEMPOTENCY_DIR_ENV = "SKELETON_RUNNER_IDEMPOTENCY_DIR"
TARGET_REGISTRY_ENV = "SKELETON_RUNNER_TARGET_REGISTRY"
ROOT_REGISTRY_ENV = "SKELETON_RUNNER_ROOT_REGISTRY"
_SAFE_REASON_RE = re.compile(r"^[A-Z0-9_]{1,80}$")


def poll_once(
    workdir: str | None = None,
    *,
    include_legacy: bool = True,
) -> int:
    issues = legacy_runner.get_ready_issues()
    processed = 0
    for issue in issues:
        issue_number = int(issue["number"])
        try:
            hydrated = _read_issue_with_author(issue_number)
            request = parse_queue_request(
                hydrated,
                trusted_authors=legacy_runner.trusted_runner_comment_authors(),
            )
        except TaskEnvelopeQueueError as exc:
            legacy_runner.block_issue(
                issue_number,
                f"Universal queue request rejected: {type(exc).__name__}",
            )
            processed += 1
            continue
        except Exception as exc:
            legacy_runner.block_issue(
                issue_number,
                f"Universal queue metadata failed: {type(exc).__name__}",
            )
            processed += 1
            continue
        if request is None:
            if include_legacy:
                legacy_runner.process_issue(issue, workdir=workdir)
                processed += 1
            continue
        _process_envelope_request(request)
        processed += 1
    return processed


def _read_issue_with_author(issue_number: int) -> dict[str, Any]:
    code, output = legacy_runner.run_command(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--repo",
            legacy_runner.REPO,
            "--json",
            "number,title,body,state,url,closed,author",
        ]
    )
    if code != 0:
        raise RuntimeError("unable to read queue issue metadata")
    value = json.loads(output or "{}")
    if not isinstance(value, dict):
        raise RuntimeError("queue issue metadata is malformed")
    return value


def _process_envelope_request(request: QueueEnvelopeRequest) -> None:
    issue_number = request.issue_number
    legacy_runner.set_issue_label(
        issue_number,
        legacy_runner.LABEL_READY,
        legacy_runner.LABEL_RUNNING,
    )
    warning = legacy_runner.record_runner_task_picked_up(
        issue_number,
        "skeleton",
        "maintenance",
    )
    try:
        envelope_path = resolve_task_envelope_file(
            TaskEnvelopeReference(
                reference_id=request.reference_id,
                content_hash=request.content_hash,
            ),
            inbox=_required_path(ENVELOPE_INBOX_ENV),
        )
        context = load_runtime_context(
            target_registry_path=_optional_path(TARGET_REGISTRY_ENV),
            root_registry_path=_optional_path(ROOT_REGISTRY_ENV),
        )
        receipt = execute_governed_envelope_file(
            envelope_path,
            context=context,
            evidence_dir=_required_path(EVIDENCE_DIR_ENV),
            idempotency_dir=_required_path(IDEMPOTENCY_DIR_ENV),
        )
        status = "DONE" if receipt.get("status") == "DONE" else "BLOCKED"
        report = _receipt_report(receipt, status=status)
    except Exception as exc:
        status = "BLOCKED"
        report = _blocked_report(exc)

    memory_warning = legacy_runner.record_runner_executor_result(
        issue_number,
        "skeleton",
        status,
        status,
        "maintenance",
        report,
    )
    report = legacy_runner.append_memory_warning(
        report,
        memory_warning or warning,
    )
    legacy_runner.post_issue_comment(issue_number, report)
    legacy_runner.set_issue_label(
        issue_number,
        legacy_runner.LABEL_RUNNING,
        (
            legacy_runner.LABEL_DONE
            if status == "DONE"
            else legacy_runner.LABEL_BLOCKED
        ),
    )
    legacy_runner.notify_task_finished(issue_number, status, report)


def _required_path(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required runtime path is not configured: {name}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"runtime path must be absolute: {name}")
    return path


def _optional_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return _required_path(name) if value else None


def _receipt_report(receipt: dict[str, Any], *, status: str) -> str:
    allowed = (
        "schema",
        "task_id",
        "envelope_hash",
        "evidence_hash",
        "executor_class",
        "risk_class",
        "privacy_class",
        "status",
        "step_count",
        "assertion_count",
        "rollback_status",
        "rollback_step_count",
    )
    public = {key: receipt.get(key) for key in allowed if key in receipt}
    return (
        f"{status}: Universal Runner TaskEnvelope completed.\n"
        "```json\n"
        + json.dumps(public, sort_keys=True, indent=2)
        + "\n```"
    )


def _blocked_report(exc: Exception) -> str:
    reason = getattr(exc, "reason_code", None)
    lines = [
        "BLOCKED: Universal Runner TaskEnvelope failed closed.",
        f"error_class={type(exc).__name__}",
    ]
    if isinstance(reason, str) and _SAFE_REASON_RE.fullmatch(reason):
        lines.append(f"reason={reason}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll universal Runner tasks.")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--envelopes-only", action="store_true")
    args = parser.parse_args()
    if args.loop:
        while True:
            poll_once(
                workdir=args.workdir,
                include_legacy=not args.envelopes_only,
            )
            time.sleep(legacy_runner.POLL_INTERVAL)
    else:
        poll_once(
            workdir=args.workdir,
            include_legacy=not args.envelopes_only,
        )


if __name__ == "__main__":
    main()
