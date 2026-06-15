from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from json import JSONDecodeError
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.hermes_worker import run_hermes_worker_dry_run


DEFAULT_TASK_PACKET = ROOT / "fixtures" / "hermes_worker" / "task_packet_dry_run_ok.json"
DEFAULT_SKILL_MANIFEST = (
    ROOT / "fixtures" / "hermes_worker" / "skill_manifest_review_only.json"
)

STATUS_LABELS = {
    "DRY_RUN_OK": "OK",
    "REVIEW_REQUIRED": "REVIEW_REQUIRED",
    "OPERATOR_APPROVAL_REQUIRED": "OPERATOR_APPROVAL_REQUIRED",
    "BLOCKED": "BLOCKED",
}

SAFE_REASONS = {
    "packet_satisfies_public_safe_dry_run_contract",
    "packet_requires_review_before_dry_run",
    "skill_tier_requires_operator_approval",
    "packet_requests_unsafe_or_live_execution",
    "input_json_invalid_or_unreadable",
    "check_failed",
}


class HermesCheckError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        task_packet = _load_json(args.packet)
        skill_manifest = _load_json(args.skill) if args.skill is not None else None
        result = run_hermes_worker_dry_run(task_packet, skill_manifest)
        status = STATUS_LABELS.get(str(result.get("status")), "BLOCKED")
        reason = _safe_reason(result)
        warning_count = _safe_warning_count(result)
    except HermesCheckError as exc:
        status = "BLOCKED"
        reason = exc.reason
        warning_count = 0
    except Exception:
        status = "BLOCKED"
        reason = "check_failed"
        warning_count = 0

    print(_render_result(status=status, reason=reason, warning_count=warning_count))
    return 0 if status == "OK" else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a public-safe manual Hermes dry-run check."
    )
    parser.add_argument(
        "--packet",
        type=Path,
        default=DEFAULT_TASK_PACKET,
        help="Public-safe Hermes task packet JSON. Defaults to the bundled fixture.",
    )
    parser.add_argument(
        "--skill",
        type=Path,
        default=DEFAULT_SKILL_MANIFEST,
        help="Public-safe Hermes skill manifest JSON. Use --no-skill to omit it.",
    )
    parser.add_argument(
        "--no-skill",
        action="store_true",
        help="Run the check without a skill manifest.",
    )
    args = parser.parse_args(argv)
    if args.no_skill:
        args.skill = None
    return args


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, JSONDecodeError) as exc:
        raise HermesCheckError("input_json_invalid_or_unreadable") from exc


def _safe_reason(result: dict[str, object]) -> str:
    decision = result.get("decision")
    reason = decision.get("reason") if isinstance(decision, dict) else None
    if isinstance(reason, str) and reason in SAFE_REASONS:
        return reason
    return "check_failed"


def _safe_warning_count(result: dict[str, object]) -> int:
    warnings = result.get("warnings")
    if isinstance(warnings, list):
        return len(warnings)
    return 0


def _render_result(*, status: str, reason: str, warning_count: int) -> str:
    safe_status = status if status in STATUS_LABELS.values() else "BLOCKED"
    safe_reason = reason if reason in SAFE_REASONS else "check_failed"
    return "\n".join(
        [
            f"HERMES_CHECK_RESULT={safe_status}",
            f"DECISION={'allowed' if safe_status == 'OK' else 'not_allowed'}",
            f"REASON={safe_reason}",
            f"WARNINGS={warning_count}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
