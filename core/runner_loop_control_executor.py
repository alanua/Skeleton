from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final


LOOP_ENGINE_PACKET: Final = "loop_engine_packet"
LOOP_STATE_DB_ENV: Final = "SKELETON_LOOP_STATE_DB"

MaintenanceReport = Callable[[str, str, list[str], str], str]


def loop_state_db_path(
    *,
    environment: Mapping[str, str],
    env_var_name: str,
    root: Path,
    path_has_symlink_component: Callable[[Path], bool],
    path_is_relative_to: Callable[[Path, Path], bool],
) -> tuple[Path | None, str | None]:
    raw_path = environment.get(env_var_name, "").strip()
    if not raw_path:
        return None, "loop_state_db_missing"

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return None, "loop_state_db_not_absolute"
    if ".." in path.parts or path_has_symlink_component(path):
        return None, "loop_state_db_unsafe"

    resolved = path.resolve(strict=False)
    if resolved == root or path_is_relative_to(resolved, root):
        return None, "loop_state_db_unsafe"

    parent = resolved.parent
    try:
        parent_stat = parent.stat()
    except OSError:
        return None, "loop_state_db_parent_unavailable"

    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or not os.access(parent, os.W_OK | os.X_OK)
    ):
        return None, "loop_state_db_not_writable"

    if resolved.exists():
        try:
            file_stat = resolved.stat()
        except OSError:
            return None, "loop_state_db_not_writable"

        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != os.getuid()
            or not os.access(resolved, os.R_OK | os.W_OK)
        ):
            return None, "loop_state_db_not_writable"

    return resolved, None


def loop_task_packet_from_body(
    body: str,
    *,
    extract_task_block: Callable[[str], str | None],
) -> object:
    task_block = extract_task_block(body)
    if task_block is None:
        return {}

    try:
        return json.loads(task_block)
    except json.JSONDecodeError:
        return {}


def loop_receipt_status_line(key: str, value: object) -> str:
    if value is None:
        rendered = "none"
    elif isinstance(value, bool):
        rendered = str(value).lower()
    elif isinstance(value, int) and not isinstance(value, bool):
        rendered = str(value)
    elif isinstance(value, str):
        rendered = value
    else:
        rendered = "invalid"

    return f"{key}={rendered}"


def loop_receipt_report(
    receipt: object,
    *,
    task_id: str,
    maintenance_report: MaintenanceReport,
) -> str:
    expected_keys = (
        "schema",
        "status",
        "action",
        "task_id",
        "run_id",
        "version",
        "loop_state",
        "event",
        "accepted",
        "decision",
        "reason",
        "context_hash",
        "public_safe",
        "external_side_effects_executed",
    )

    if not isinstance(receipt, dict) or set(receipt) != set(expected_keys):
        return maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=loop_receipt_schema_mismatch"],
            "not_met",
        )

    status_lines = [
        loop_receipt_status_line(key, receipt.get(key))
        for key in expected_keys
    ]

    accepted = receipt.get("accepted") is True
    decision = receipt.get("decision")
    loop_state = receipt.get("loop_state")
    receipt_status = receipt.get("status")

    if not accepted or decision == "REJECT" or receipt_status == "BLOCKED":
        report_status = "BLOCKED"
    elif decision in {"ESCALATE", "REVIEW"} or loop_state in {
        "NEEDS_OPERATOR",
        "HUMAN_REVIEW",
    }:
        report_status = "NEEDS_OPERATOR"
    elif loop_state in {"BLOCKED", "CANCELLED"}:
        report_status = "BLOCKED"
    else:
        report_status = "DONE"

    return maintenance_report(
        report_status,
        task_id,
        status_lines,
        "met" if report_status == "DONE" else "not_met",
    )


def execute_loop_engine_packet(
    body: str,
    *,
    task_id: str,
    state_db_path: Callable[[], tuple[Path | None, str | None]],
    task_packet_from_body: Callable[[str], object],
    receipt_report: Callable[[object], str],
    maintenance_report: MaintenanceReport,
    store_factory: Callable[[Path], Any],
    engine_factory: Callable[[Any, Any], Any],
    policy_factory: Callable[[], Any],
    packet_runner: Callable[..., object],
) -> str:
    db_path, reason = state_db_path()
    if reason is not None or db_path is None:
        return maintenance_report(
            "BLOCKED",
            task_id,
            [f"reason={reason or 'loop_state_db_unavailable'}"],
            "not_met",
        )

    packet = task_packet_from_body(body)

    try:
        store = store_factory(db_path)
        store.initialize()
        engine = engine_factory(store, policy_factory())
        receipt = packet_runner(packet, engine=engine)
    except Exception:
        return maintenance_report(
            "BLOCKED",
            task_id,
            ["reason=loop_engine_packet_failed"],
            "not_met",
        )

    return receipt_report(receipt)
