from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from core.video_artifact_store import (
    PrivateVideoArtifactStore,
    VideoArtifactStoreError,
    public_receipt,
    reject_private_public_receipt,
)
from core.video_provider_registry import synthetic_local_provider_registry
from core.video_understanding_queue import VideoUnderstandingQueue
from scripts.video_understanding_worker import run_local_first_task_once


class SyntheticGateway:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}
        self.commands: list[str] = []

    def execute(self, request: Mapping[str, object]) -> dict[str, object]:
        command = str(request["command"])
        self.commands.append(command)
        payload = request["payload"]
        assert isinstance(payload, Mapping)
        if command == "skeleton.memory.private_mutate":
            assert payload["dataset_id"] == "video_understanding"
            self.records[str(payload["key"])] = payload["value"]
            return {"status": "COMMITTED"}
        if command == "skeleton.memory.private_read_exact":
            return {"status": "FOUND", "value": self.records[str(payload["key"])]}
        raise AssertionError(command)


def test_local_first_pipeline_idempotent_memorygateway_readback(tmp_path: Path) -> None:
    queue = VideoUnderstandingQueue(tmp_path / "queue")
    registry = synthetic_local_provider_registry(tmp_path)
    store = PrivateVideoArtifactStore(tmp_path / "artifacts")
    gateway = SyntheticGateway()
    task = queue.enqueue(
        operation="video_process_one",
        idempotency_key="synthetic-video-a",
        payload={
            "source_ref": "synthetic_source",
            "frames": ["frame-bytes"],
            "audios": ["audio-bytes"],
            "ocrs": ["synthetic visible title"],
            "transcripts": ["synthetic spoken words"],
            "model_outputs": ["stable synthetic conclusion"],
        },
    )

    first = run_local_first_task_once(
        queue=queue,
        registry=registry,
        artifact_store=store,
        memory_gateway=gateway,
        worker_id="worker-1",
    )
    replay = queue.enqueue(operation="video_process_one", idempotency_key="synthetic-video-a", payload={})

    assert first["status"] == "DONE"
    assert first["artifact_count"] == 5
    assert first["canonical_mutation_status"] == "COMMITTED"
    assert task.task_id == replay.task_id
    assert queue.counts()["done"] == 1
    assert gateway.commands == [
        "skeleton.memory.private_mutate",
        "skeleton.memory.private_read_exact",
    ]


def test_ambiguous_output_goes_to_review_and_does_not_mutate_memory(tmp_path: Path) -> None:
    queue = VideoUnderstandingQueue(tmp_path / "queue")
    task = queue.enqueue(
        operation="video_process_one",
        idempotency_key="synthetic-ambiguous",
        payload={"ambiguous": True, "frames": ["maybe"], "transcripts": ["unclear"]},
    )
    gateway = SyntheticGateway()

    receipt = run_local_first_task_once(
        queue=queue,
        registry=synthetic_local_provider_registry(tmp_path),
        artifact_store=PrivateVideoArtifactStore(tmp_path / "artifacts"),
        memory_gateway=gateway,
        worker_id="worker-1",
    )

    assert receipt["status"] == "REVIEW"
    assert receipt["review_required"] is True
    assert receipt["canonical_mutation_status"] == "NOT_REQUESTED"
    assert queue.counts()["review"] == 1
    assert queue.counts()["done"] == 0
    assert task.task_id not in gateway.records


def test_private_like_output_rejected_from_public_receipts() -> None:
    receipt = public_receipt(
        operation="video_process_one",
        status="DONE",
        reason_code="LOCAL_FIRST_COMPLETE",
        counts={"frame_count": 1},
        review_required=False,
        canonical_mutation_status="COMMITTED",
    )
    assert receipt["frame_count"] == 1

    with pytest.raises(VideoArtifactStoreError) as exc:
        reject_private_public_receipt({"status": "DONE", "leak": "/private/media/frame001.png"})
    assert exc.value.reason_code == "PUBLIC_RECEIPT_PRIVATE_DATA"
