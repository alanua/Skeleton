from __future__ import annotations

from core.resolver_registry.failure import FailureTracker, classify_failure, sanitize_evidence


def test_origin_protected_creates_cooldown_and_no_runner_task() -> None:
    tracker = FailureTracker(structural_threshold=2, cooldown_seconds=600)
    decision = tracker.observe(
        {
            "host": "anitube.in.ua",
            "error_type": "origin_protected",
            "error_detail": "Authorization: secret Cookie: session signed=https://cdn/x.m3u8?token=abc",
            "runtime_version": "cast-1",
            "adapter_chain": ["documented_api", "structured_data", "rendered_dom"],
            "negative_knowledge": {"method": "chrome", "Cookie": "secret"},
        }
    )

    assert decision.action == "cooldown"
    assert decision.cooldown_seconds == 600
    assert decision.runner_task is None
    assert decision.evidence["failure_class"] == "origin_protected"
    assert "secret" not in str(decision.evidence)


def test_repeated_structural_parser_failure_creates_one_deduplicated_task() -> None:
    tracker = FailureTracker(structural_threshold=3)
    event = {
        "host": "example.test",
        "error_type": "parser",
        "error_detail": "parser xpath no selector",
        "runtime_version": "cast-1",
        "adapter_chain": ["structured_data", "standard_embed"],
        "negative_knowledge": {"selector": ".missing"},
    }

    assert tracker.observe(event).action == "record_only"
    assert tracker.observe(event).action == "record_only"
    created = tracker.observe(event)
    duplicate = tracker.observe(event)

    assert created.action == "create_runner_task"
    assert created.runner_task is not None
    assert created.runner_task["schema"] == "skeleton.runner_task.v1"
    assert created.runner_task["payload"]["preferred_order"][0] == "documented_api"
    assert duplicate.action == "deduplicated"


def test_failure_classification_covers_transient_classes() -> None:
    assert classify_failure("network", "curl: failed to connect") == "network"
    assert classify_failure("timeout", "timed out") == "timeout"
    assert classify_failure("rate_limit", "rate limit") == "rate_limit"


def test_sanitized_evidence_is_bounded_and_secret_free() -> None:
    evidence = sanitize_evidence(
        {
            "site_host": "media.example",
            "error_detail": "x" * 3000 + " token=abc",
            "headers": {"Authorization": "secret"},
            "negative_knowledge": {"headers": "secret", "cooldown": 10},
        },
        max_diagnostic_bytes=128,
    )

    assert len(evidence["diagnostics"]) == 128
    assert "headers" not in evidence["negative_knowledge"]
    assert evidence["negative_knowledge"]["cooldown"] == 10
