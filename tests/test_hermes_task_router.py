from __future__ import annotations

import pytest

from core.hermes_task_router import (
    HermesTaskRouterError,
    build_hermes_task_packet,
    validate_hermes_task_packet,
)


def packet(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "task_id": "ISSUE-1039",
        "title": "Repair public-safe Hermes provider routing",
        "goal": "Build a static sanitized task packet for contract testing.",
        "route_alias": "LOW",
        "budget_units": 20,
        "max_retries": 1,
        "token_cap": 12_000,
        "chunk_token_cap": 2_000,
    }
    args.update(overrides)
    return build_hermes_task_packet(**args)


@pytest.mark.parametrize(
    ("alias", "role"),
    [
        ("LOW", "bulk_worker"),
        ("MID", "planner"),
        ("HIGH", "critic"),
    ],
)
def test_alias_routing_is_static_example_only(alias: str, role: str) -> None:
    routed = packet(route_alias=alias)

    assert routed["provider_route"]["route_alias"] == alias
    assert routed["provider_route"]["provider_role"] == role
    assert routed["provider_route"]["provider_example_only"] is True
    assert routed["provider_route"]["live_route_enabled"] is False
    assert routed["no_runtime_mutation"] is True
    assert all(command["mutating"] is False for command in routed["validation"])


def test_emitted_packet_validates_against_task_packet_schema() -> None:
    routed = packet(route_alias="MID", notes="Synthetic sanitized review packet.")

    validate_hermes_task_packet(routed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("goal", "Review https://drive.google.com/file/d/private-id/view"),
        ("goal", "Review /home/operator/private/project.pdf"),
        ("title", "Use token sk-live-secret-token in the prompt"),
        ("source_reference", "C:\\Users\\operator\\private\\drawing.pdf"),
    ],
)
def test_rejects_url_path_and_secret_like_public_inputs(field: str, value: str) -> None:
    with pytest.raises(HermesTaskRouterError):
        packet(**{field: value})


def test_rejects_non_public_or_mutating_input_fields_before_return() -> None:
    with pytest.raises(HermesTaskRouterError):
        packet(notes={"deploy": "publish the result"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"budget_units": 0},
        {"budget_units": 1001},
        {"max_retries": -1},
        {"max_retries": 4},
        {"token_cap": 0},
        {"token_cap": 200_001},
        {"chunk_token_cap": 20_001},
        {"chunk_token_cap": 12_001, "token_cap": 12_000},
    ],
)
def test_validates_budget_retry_and_token_bounds(overrides: dict[str, object]) -> None:
    with pytest.raises(HermesTaskRouterError):
        packet(**overrides)


def test_schema_validation_blocks_mutating_emitted_packet() -> None:
    routed = packet()
    routed["validation"][0]["mutating"] = True

    with pytest.raises(HermesTaskRouterError):
        validate_hermes_task_packet(routed)


def test_schema_validation_blocks_live_provider_route() -> None:
    routed = packet()
    routed["provider_route"]["live_route_enabled"] = True

    with pytest.raises(HermesTaskRouterError):
        validate_hermes_task_packet(routed)
