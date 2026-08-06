#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.video_understanding.live_runtime import (
    build_live_runtime,
    doctor_live_runtime,
    synthetic_memory_roundtrip,
)
from core.video_understanding.models import VideoUnderstandingError
from core.video_understanding.pipeline import VideoPipeline
from core.video_understanding.queue import FileQueue
from core.video_understanding.worker import VideoWorker
from core.video_artifact_store import PrivateVideoArtifactStore, public_receipt
from core.video_provider_registry import LocalProviderRegistry
from core.video_understanding_queue import VideoQueueError, VideoUnderstandingQueue


_PRIVATE_MUTATE_SCHEMA = "skeleton.private_memory_gateway.mutation.v1"
_MEMORY_REQUEST_SCHEMA = "skeleton.memory_gateway.request.v1"


def run_local_first_task_once(
    *,
    queue: VideoUnderstandingQueue,
    registry: LocalProviderRegistry,
    artifact_store: PrivateVideoArtifactStore,
    memory_gateway: object,
    worker_id: str,
) -> Mapping[str, object]:
    """Synthetic local-first worker path used by review-time tests.

    Runtime activation remains separate; this helper performs no service start and
    accepts only injected local providers, private artifact storage and a gateway.
    """
    with queue.single_worker(worker_id):
        task = queue.claim(worker_id)
        if task is None:
            return public_receipt(
                operation="video_process_one",
                status="IDLE",
                reason_code="QUEUE_EMPTY",
                counts={},
                review_required=False,
                canonical_mutation_status="NOT_REQUESTED",
            )
        try:
            payload = dict(task.payload)
            ambiguous = bool(payload.get("ambiguous"))
            providers = {
                "extractor": registry.route("extractor", str(payload.get("extractor_provider", "ffmpeg-local"))),
                "ocr": registry.route("ocr", str(payload.get("ocr_provider", "tesseract-local"))),
                "transcription": registry.route("transcription", str(payload.get("transcription_provider", "whisper-local"))),
                "model": registry.route("model", str(payload.get("model_provider", "ollama-local"))),
            }
            artifacts = _store_synthetic_artifacts(payload, artifact_store)
            if ambiguous:
                queue.complete(task.task_id, worker_id, ambiguous=True)
                return public_receipt(
                    operation=task.operation,
                    status="REVIEW",
                    reason_code="AMBIGUOUS_REVIEW",
                    counts=_counts(artifacts),
                    review_required=True,
                    canonical_mutation_status="NOT_REQUESTED",
                )
            mutation = _memory_mutation(task.task_id, payload, artifacts, providers)
            mutation_result = memory_gateway.execute(mutation)  # type: ignore[attr-defined]
            lookup_result = memory_gateway.execute(_memory_readback(task.task_id))  # type: ignore[attr-defined]
            if not isinstance(mutation_result, Mapping) or not isinstance(lookup_result, Mapping):
                raise VideoQueueError("MEMORY_GATEWAY_INVALID", "MemoryGateway result is invalid")
            queue.complete(task.task_id, worker_id)
            return public_receipt(
                operation=task.operation,
                status="DONE",
                reason_code="LOCAL_FIRST_COMPLETE",
                counts=_counts(artifacts),
                review_required=False,
                canonical_mutation_status="COMMITTED",
            )
        except Exception as exc:
            reason = getattr(exc, "reason_code", type(exc).__name__).upper()
            safe_reason = reason if reason.replace("_", "").isalnum() else "WORKER_FAILURE"
            failed = queue.fail(task.task_id, worker_id, reason_code=safe_reason)
            return public_receipt(
                operation=task.operation,
                status="QUARANTINED" if failed.state == "quarantined" else "RETRY",
                reason_code=safe_reason,
                counts={},
                review_required=False,
                canonical_mutation_status="NOT_REQUESTED",
            )


def _store_synthetic_artifacts(
    payload: Mapping[str, object],
    artifact_store: PrivateVideoArtifactStore,
) -> tuple[Mapping[str, object], ...]:
    artifacts: list[Mapping[str, object]] = []
    for kind in ("frame", "audio", "ocr", "transcript", "model_output"):
        values = payload.get(kind + "s", [])
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            if not isinstance(value, str):
                raise VideoQueueError("SYNTHETIC_PAYLOAD_INVALID", "synthetic artifact must be text")
            stored = artifact_store.put_bytes(
                kind=kind,
                payload=value.encode("utf-8"),
                provenance={"task": "synthetic", "index": index, "kind": kind},
                confidence=float(payload.get("confidence", 0.8)),
            )
            artifact_store.readback(stored)
            public = stored.public_ref()
            public["kind"] = kind
            artifacts.append(public)
    return tuple(artifacts)


def _counts(artifacts: tuple[Mapping[str, object], ...]) -> dict[str, int]:
    counts = {
        "frame_count": 0,
        "audio_count": 0,
        "ocr_count": 0,
        "transcript_count": 0,
        "model_output_count": 0,
        "artifact_count": len(artifacts),
    }
    for artifact in artifacts:
        key = str(artifact.get("kind")) + "_count"
        if key in counts:
            counts[key] += 1
    return counts


def _memory_mutation(
    task_id: str,
    payload: Mapping[str, object],
    artifacts: tuple[Mapping[str, object], ...],
    providers: Mapping[str, object],
) -> Mapping[str, object]:
    provider_names = {kind: getattr(provider, "name") for kind, provider in providers.items()}
    return {
        "schema": _MEMORY_REQUEST_SCHEMA,
        "namespace": "skeleton",
        "command": "skeleton.memory.private_mutate",
        "payload": {
            "schema": _PRIVATE_MUTATE_SCHEMA,
            "operation": "put",
            "dataset_id": "video_understanding",
            "key": task_id,
            "value": {
                "schema": "skeleton.video_understanding.synthetic_fact.v1",
                "task_id": task_id,
                "source_ref": payload.get("source_ref", "synthetic_source"),
                "artifact_refs": list(artifacts),
                "providers": provider_names,
            },
        },
    }


def _memory_readback(task_id: str) -> Mapping[str, object]:
    return {
        "schema": _MEMORY_REQUEST_SCHEMA,
        "namespace": "skeleton",
        "command": "skeleton.memory.private_read_exact",
        "payload": {"dataset_id": "video_understanding", "key": task_id},
    }


def run_once(
    queue: FileQueue,
    pipeline: VideoPipeline,
    *,
    worker_id: str,
) -> Mapping[str, object]:
    return VideoWorker(queue, pipeline, worker_id=worker_id).run_once()


def run_forever(
    queue: FileQueue,
    pipeline: VideoPipeline,
    *,
    worker_id: str,
    poll_seconds: float = 5.0,
) -> None:
    VideoWorker(queue, pipeline, worker_id=worker_id).run_forever(
        poll_seconds=poll_seconds
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the private Skeleton Video Understanding worker."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--processing-revision", required=True)
    parser.add_argument("--worker-id", default="hetzner-video-worker-1")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--doctor", action="store_true")
    mode.add_argument("--memory-roundtrip", action="store_true")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--forever", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.doctor:
            _print_public(
                doctor_live_runtime(
                    args.config,
                    processing_revision=args.processing_revision,
                )
            )
            return 0
        runtime = build_live_runtime(
            args.config,
            processing_revision=args.processing_revision,
        )
        if args.memory_roundtrip:
            receipt = synthetic_memory_roundtrip(
                runtime,
                approval_ref="operator.video.runtime.launch",
            )
            _print_public(receipt)
            return 0 if receipt.get("status") == "DONE" else 2
        if args.once:
            _print_public(
                run_once(runtime.queue, runtime.pipeline, worker_id=args.worker_id)
            )
            return 0
        if not 0.5 <= args.poll_seconds <= 300:
            raise VideoUnderstandingError(
                "POLL_INTERVAL_INVALID",
                "worker poll interval is invalid",
            )
        _print_public(
            {
                "schema": "skeleton.video_understanding.worker_start.v1",
                "status": "RUNNING",
                "worker_count": 1,
                "queue_counts": runtime.queue.counts(),
            }
        )
        run_forever(
            runtime.queue,
            runtime.pipeline,
            worker_id=args.worker_id,
            poll_seconds=args.poll_seconds,
        )
        return 0
    except VideoUnderstandingError as exc:
        _print_public(
            {
                "schema": "skeleton.video_understanding.worker_error.v1",
                "status": "BLOCKED",
                "reason_code": exc.reason_code,
            }
        )
        return 2
    except Exception as exc:
        _print_public(
            {
                "schema": "skeleton.video_understanding.worker_error.v1",
                "status": "BLOCKED",
                "reason_code": type(exc).__name__,
            }
        )
        return 2


def _print_public(payload: Mapping[str, object]) -> None:
    print(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
