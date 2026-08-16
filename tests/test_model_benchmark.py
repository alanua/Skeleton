from __future__ import annotations

from core.model_benchmark import BenchmarkReceipt, summarize_capability


def test_external_benchmark_alone_cannot_mark_live() -> None:
    record = summarize_capability(
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


def test_skeleton_canary_pass_marks_capability_live() -> None:
    record = summarize_capability(
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


def test_hard_failure_is_not_averaged_away() -> None:
    record = summarize_capability(
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
    assert "DELIVERABLE_MISSING" in record.hard_failures
