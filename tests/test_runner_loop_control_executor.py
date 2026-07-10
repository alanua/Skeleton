from __future__ import annotations

import json
from pathlib import Path

from core import runner_loop_control_executor as executor
from scripts import runner_poll_github_tasks as runner


ROOT = Path(__file__).resolve().parents[1]


def report_builder(
    status: str,
    task_id: str,
    lines: list[str],
    success_criteria: str,
) -> str:
    return json.dumps(
        {
            "status": status,
            "task_id": task_id,
            "lines": lines,
            "success_criteria": success_criteria,
        },
        sort_keys=True,
    )


def receipt(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "loop.receipt.v1",
        "status": "DONE",
        "action": "continue",
        "task_id": "task-1",
        "run_id": "run-1",
        "version": 1,
        "loop_state": "RUNNING",
        "event": "iteration_completed",
        "accepted": True,
        "decision": "CONTINUE",
        "reason": "policy_continue",
        "context_hash": "a" * 64,
        "public_safe": True,
        "external_side_effects_executed": False,
    }
    value.update(updates)
    return value


def test_executor_module_has_no_poller_import() -> None:
    source = Path(executor.__file__).read_text(encoding="utf-8")

    assert "runner_poll_github_tasks" not in source


def test_state_db_path_accepts_owned_writable_external_path(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "loop.sqlite"

    resolved, reason = executor.loop_state_db_path(
        environment={
            executor.LOOP_STATE_DB_ENV: str(db_path),
        },
        env_var_name=executor.LOOP_STATE_DB_ENV,
        root=ROOT,
        path_has_symlink_component=lambda _path: False,
        path_is_relative_to=lambda child, parent: (
            child == parent or parent in child.parents
        ),
    )

    assert resolved == db_path.resolve()
    assert reason is None


def test_state_db_path_rejects_relative_path() -> None:
    resolved, reason = executor.loop_state_db_path(
        environment={
            executor.LOOP_STATE_DB_ENV: "relative.sqlite",
        },
        env_var_name=executor.LOOP_STATE_DB_ENV,
        root=ROOT,
        path_has_symlink_component=lambda _path: False,
        path_is_relative_to=lambda child, parent: (
            child == parent or parent in child.parents
        ),
    )

    assert resolved is None
    assert reason == "loop_state_db_not_absolute"


def test_packet_parser_fails_closed() -> None:
    assert executor.loop_task_packet_from_body(
        "body",
        extract_task_block=lambda _body: None,
    ) == {}

    assert executor.loop_task_packet_from_body(
        "body",
        extract_task_block=lambda _body: "{malformed",
    ) == {}


def test_receipt_report_preserves_status_mapping() -> None:
    done = json.loads(
        executor.loop_receipt_report(
            receipt(),
            task_id=executor.LOOP_ENGINE_PACKET,
            maintenance_report=report_builder,
        )
    )
    blocked = json.loads(
        executor.loop_receipt_report(
            receipt(accepted=False),
            task_id=executor.LOOP_ENGINE_PACKET,
            maintenance_report=report_builder,
        )
    )
    review = json.loads(
        executor.loop_receipt_report(
            receipt(decision="REVIEW"),
            task_id=executor.LOOP_ENGINE_PACKET,
            maintenance_report=report_builder,
        )
    )

    assert done["status"] == "DONE"
    assert done["success_criteria"] == "met"
    assert blocked["status"] == "BLOCKED"
    assert blocked["success_criteria"] == "not_met"
    assert review["status"] == "NEEDS_OPERATOR"


def test_receipt_schema_mismatch_fails_closed() -> None:
    result = json.loads(
        executor.loop_receipt_report(
            {"status": "DONE"},
            task_id=executor.LOOP_ENGINE_PACKET,
            maintenance_report=report_builder,
        )
    )

    assert result["status"] == "BLOCKED"
    assert result["lines"] == [
        "reason=loop_receipt_schema_mismatch"
    ]


def test_execute_loop_packet_uses_injected_route_dependencies() -> None:
    events: list[object] = []

    class Store:
        def __init__(self, path: Path) -> None:
            events.append(("store", path))

        def initialize(self) -> None:
            events.append("initialize")

    class Engine:
        def __init__(self, store: object, policy: object) -> None:
            events.append(("engine", store, policy))

    result = executor.execute_loop_engine_packet(
        "packet body",
        task_id=executor.LOOP_ENGINE_PACKET,
        state_db_path=lambda: (Path("/tmp/loop.sqlite"), None),
        task_packet_from_body=lambda body: {"body": body},
        receipt_report=lambda value: f"receipt={value['ok']}",
        maintenance_report=report_builder,
        store_factory=Store,
        engine_factory=Engine,
        policy_factory=lambda: "policy",
        packet_runner=lambda packet, *, engine, trusted_recovery_approvals: {
            "ok": packet == {"body": "packet body"}
            and engine is not None
            and trusted_recovery_approvals == ()
        },
    )

    assert result == "receipt=True"
    assert events[0] == ("store", Path("/tmp/loop.sqlite"))
    assert events[1] == "initialize"


def test_execute_loop_packet_failure_remains_fail_closed() -> None:
    result = json.loads(
        executor.execute_loop_engine_packet(
            "packet body",
            task_id=executor.LOOP_ENGINE_PACKET,
            state_db_path=lambda: (Path("/tmp/loop.sqlite"), None),
            task_packet_from_body=lambda _body: {},
            receipt_report=lambda _receipt: "unexpected",
            maintenance_report=report_builder,
            store_factory=lambda _path: (_ for _ in ()).throw(
                RuntimeError("failure")
            ),
            engine_factory=lambda _store, _policy: object(),
            policy_factory=object,
            packet_runner=lambda _packet, *, engine, trusted_recovery_approvals: engine,
        )
    )

    assert result["status"] == "BLOCKED"
    assert result["lines"] == [
        "reason=loop_engine_packet_failed"
    ]


def test_poller_wrapper_preserves_existing_monkeypatch_surface(
    monkeypatch,
) -> None:
    class Store:
        def __init__(self, _path: Path) -> None:
            pass

        def initialize(self) -> None:
            pass

    monkeypatch.setattr(
        runner,
        "_loop_state_db_path",
        lambda: (Path("/tmp/loop.sqlite"), None),
    )
    monkeypatch.setattr(
        runner,
        "_loop_task_packet_from_body",
        lambda _body: {"packet": True},
    )
    monkeypatch.setattr(
        runner,
        "_loop_receipt_report",
        lambda value: f"receipt={value['result']}",
    )
    monkeypatch.setattr(runner, "LoopStateStore", Store)
    monkeypatch.setattr(
        runner,
        "LoopEngine",
        lambda _store, _policy: "engine",
    )
    monkeypatch.setattr(runner, "LoopPolicy", lambda: "policy")
    monkeypatch.setattr(
        runner,
        "run_loop_task_packet",
        lambda packet, *, engine, trusted_recovery_approvals: {
            "result": packet == {"packet": True}
            and engine == "engine"
            and trusted_recovery_approvals == ()
        },
    )

    assert runner.loop_engine_packet("body") == "receipt=True"
