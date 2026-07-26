from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from pathlib import Path

from core.family_document_local_inference import FamilyDocumentHandoffIngestor
from core.local_inference_adapters import build_default_registry
from core.local_inference_runtime import (
    FileLock,
    InferenceQueue,
    InferenceRuntimeError,
    LocalInferenceWorker,
    OllamaClient,
)


def _queue_root(value: str | None) -> Path:
    resolved = (value or os.environ.get("SKELETON_LOCAL_INFERENCE_ROOT", "")).strip()
    if not resolved:
        raise SystemExit("local inference root missing")
    return Path(resolved).expanduser().resolve()


def _models(value: str | None) -> set[str]:
    raw = value or os.environ.get("SKELETON_LOCAL_INFERENCE_MODELS", "qwen2.5:1.5b")
    models = {item.strip() for item in raw.split(",") if item.strip()}
    if not models:
        raise SystemExit("model allowlist missing")
    return models


def _subject_aliases(path: str | None) -> tuple[str, str, str]:
    if not path:
        raise SystemExit("family subject aliases file missing")
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list) or len(value) != 3 or any(not isinstance(item, str) for item in value):
        raise SystemExit("family subject aliases file invalid")
    aliases = tuple(item.strip() for item in value)
    if any(not item for item in aliases) or len(set(aliases)) != 3:
        raise SystemExit("family subject aliases file invalid")
    return aliases  # type: ignore[return-value]


def _worker(args: argparse.Namespace) -> tuple[InferenceQueue, LocalInferenceWorker, FamilyDocumentHandoffIngestor | None]:
    queue = InferenceQueue(_queue_root(args.root))
    client = OllamaClient(args.endpoint or os.environ.get("SKELETON_OLLAMA_ENDPOINT", "http://127.0.0.1:11434"))
    worker = LocalInferenceWorker(
        queue,
        build_default_registry(),
        client,
        allowed_models=_models(args.models),
    )
    handoff_root = (
        args.mfp_handoff_root
        or os.environ.get("SKELETON_MFP_INFERENCE_HANDOFF_ROOT", "")
    ).strip()
    ingestor = (
        FamilyDocumentHandoffIngestor(
            handoff_root,
            queue,
            model=os.environ.get("SKELETON_LOCAL_INFERENCE_DEFAULT_MODEL", "qwen2.5:1.5b"),
            allowed_subject_aliases=_subject_aliases(
                args.family_subject_aliases_file
                or os.environ.get("SKELETON_FAMILY_SUBJECT_ALIASES_FILE")
            ),
        )
        if handoff_root
        else None
    )
    return queue, worker, ingestor


def _print_public(value: dict[str, object]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "once", "status"))
    parser.add_argument("--root")
    parser.add_argument("--endpoint")
    parser.add_argument("--models")
    parser.add_argument("--mfp-handoff-root")
    parser.add_argument("--family-subject-aliases-file")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--stale-seconds", type=int, default=600)
    args = parser.parse_args()

    queue, worker, ingestor = _worker(args)
    if args.command == "status":
        status = queue.status()
        if ingestor is not None:
            status["mfp_handoff_counts"] = ingestor.status()
        _print_public(status)
        return 0

    try:
        with FileLock(queue.worker_lock, nonblocking=True):
            queue.recover_stale_processing(stale_after_seconds=args.stale_seconds)
            if args.command == "once":
                ingested = ingestor.ingest_one() if ingestor is not None else False
                processed = worker.process_one()
                status = queue.status()
                status["ingested"] = ingested
                status["processed"] = processed
                _print_public(status)
                return 0

            stop = threading.Event()

            def handle_signal(_signum: int, _frame: object) -> None:
                stop.set()

            signal.signal(signal.SIGTERM, handle_signal)
            signal.signal(signal.SIGINT, handle_signal)
            while not stop.is_set():
                queue.recover_stale_processing(stale_after_seconds=args.stale_seconds)
                ingested = ingestor.ingest_one() if ingestor is not None else False
                processed = worker.process_one()
                if not ingested and not processed:
                    stop.wait(max(0.1, args.poll_seconds))
            return 0
    except InferenceRuntimeError as exc:
        _print_public(
            {
                "schema": "skeleton.local_inference.worker_receipt.v1",
                "status": "BLOCKED",
                "reason": exc.reason_code,
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
