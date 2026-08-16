from __future__ import annotations

from core.model_benchmark import BenchmarkReceipt, summarize_capability


def test_external_benchmark_alone_stays_discovered() -> None:
    record = summarize_capability(
        "candidate",
        "reasoning",
        [
            BenchmarkReceipt(
                "external-1",
                "candidate",
                "reasoning",
                "external_benchmark",
                True,
                0.99,
            )
        ],
    )
    assert record.status == "DEGRADED"
    assert record.canary_passed is False
    assert record.promotion_stage == "DISCOVERED"


def test_skeleton_canary_pass_marks_capability_eligible_not_live_authority() -> None:
    record = summarize_capability(
        "candidate",
        "reasoning",
        [
            BenchmarkReceipt(
                "canary-1",
                "candidate",
                "reasoning",
                "skeleton_canary",
                True,
                0.81,
            )
        ],
    )
    assert record.status == "LIVE"
    assert record.canary_passed is True
    assert record.promotion_stage == "ELIGIBLE"
    assert not record.production_eligible(0.0)


def test_failed_nonhard_canary_remains_canary_only() -> None:
    record = summarize_capability(
        "candidate",
        "reasoning",
        [
            BenchmarkReceipt(
                "canary-fail",
                "candidate",
                "reasoning",
                "skeleton_canary",
                False,
                0.40,
            )
        ],
    )
    assert record.status == "DEGRADED"
    assert record.promotion_stage == "CANARY_ONLY"


def test_hard_failure_is_not_averaged_away() -> None:
    record = summarize_capability(
        "candidate",
        "repository_edit",
        [
            BenchmarkReceipt(
                "external-good",
                "candidate",
                "repository_edit",
                "external_benchmark",
                True,
                0.98,
            ),
            BenchmarkReceipt(
                "artifact-fail",
                "candidate",
                "repository_edit",
                "skeleton_canary",
                False,
                0.60,
                ("DELIVERABLE_MISSING",),
            ),
        ],
    )
    assert record.status == "UNSUPPORTED"
    assert record.promotion_stage == "UNSUPPORTED"
    assert "DELIVERABLE_MISSING" in record.hard_failures


def test_evidence_is_isolated_by_model_identity() -> None:
    receipts = [
        BenchmarkReceipt(
            "model-a-pass",
            "model-a",
            "repository_edit",
            "skeleton_canary",
            True,
            0.90,
        ),
        BenchmarkReceipt(
            "model-b-fail",
            "model-b",
            "repository_edit",
            "skeleton_canary",
            False,
            0.70,
            ("DELIVERABLE_MISSING",),
        ),
    ]
    model_a = summarize_capability("model-a", "repository_edit", receipts)
    model_b = summarize_capability("model-b", "repository_edit", receipts)

    assert model_a.status == "LIVE"
    assert model_a.promotion_stage == "ELIGIBLE"
    assert model_a.hard_failures == ()
    assert model_a.evidence_ids == ("model-a-pass",)
    assert model_b.status == "UNSUPPORTED"
    assert model_b.hard_failures == ("DELIVERABLE_MISSING",)
    assert model_b.evidence_ids == ("model-b-fail",)
