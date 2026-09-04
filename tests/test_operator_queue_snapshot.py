from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from core.operator_queue_snapshot import (
    OPERATOR_QUEUE_SNAPSHOT_SCHEMA,
    build_operator_queue_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]


def issue(
    number: int,
    labels: list[object],
    *,
    title: str = "secret token should not be copied",
    body: str = "payload: password=hidden",
    url: str | None = None,
    **extra: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "number": number,
        "title": title,
        "body": body,
        "state": "OPEN",
        "closed": False,
        "labels": labels,
        "url": url or f"https://github.com/alanua/Skeleton/issues/{number}",
    }
    value.update(extra)
    return value


def test_snapshot_counts_runner_lifecycle_without_private_payloads() -> None:
    snapshot = build_operator_queue_snapshot(
        [
            issue(14, [{"name": "runner:running"}]),
            issue(11, [{"name": "runner:ready"}]),
            issue(12, ["runner:blocked"]),
            issue(13, ["runner:done"], closed=True, state="CLOSED"),
            issue(15, ["runner:waiting-dependency"]),
            issue(16, ["privacy:private", "runner:ready"]),
        ]
    )
    data = snapshot.as_dict()

    assert data["schema"] == OPERATOR_QUEUE_SNAPSHOT_SCHEMA
    assert data["public_safe"] is True
    assert data["counts"] == {
        "active": 4,
        "ready": 1,
        "running": 1,
        "blocked": 2,
        "done": 1,
    }
    assert data["status_uk"] == "В роботі"
    assert data["redacted_private_count"] == 1

    serialized = snapshot.to_json()
    assert "secret" not in serialized
    assert "password" not in serialized
    assert "payload" not in serialized
    assert "title" not in serialized
    assert "body" not in serialized


def test_snapshot_is_deterministic_and_prioritizes_actionable_items() -> None:
    first = build_operator_queue_snapshot(
        [
            issue(30, ["runner:done"]),
            issue(22, ["runner:ready"]),
            issue(21, ["runner:running"]),
            issue(23, ["runner:blocked"]),
        ]
    )
    second = build_operator_queue_snapshot(
        [
            issue(23, ["runner:blocked"], title="different private title"),
            issue(21, ["runner:running"], body="different private body"),
            issue(30, ["runner:done"]),
            issue(22, ["runner:ready"]),
        ]
    )

    assert first.to_json() == second.to_json()
    assert [item["issue_ref"] for item in first.as_dict()["items"]] == [
        "#21",
        "#23",
        "#22",
        "#30",
    ]


def test_snapshot_ignores_pull_requests_and_unsafe_urls() -> None:
    snapshot = build_operator_queue_snapshot(
        [
            issue(40, ["runner:ready"], url="https://github.com/alanua/Skeleton/pull/40"),
            issue(41, ["queue:RUN_NOW"], url="https://example.test/issues/41?token=hidden"),
            issue(42, ["runner:done"]),
        ]
    )
    data = snapshot.as_dict()

    assert data["counts"] == {
        "active": 1,
        "ready": 1,
        "running": 0,
        "blocked": 0,
        "done": 1,
    }
    assert [item["issue_ref"] for item in data["items"]] == ["#41", "#42"]
    assert "url" not in data["items"][0]


def test_operator_queue_snapshot_schema_matches_model() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "operator_queue_snapshot.schema.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot = build_operator_queue_snapshot([issue(51, ["runner:ready"])])

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(snapshot.as_dict())
    assert schema["properties"]["schema"]["const"] == OPERATOR_QUEUE_SNAPSHOT_SCHEMA
