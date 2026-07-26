from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from core.family_document_local_inference import (
    REQUEST_TYPE as FAMILY_DOCUMENT_REQUEST_TYPE,
    bind_family_subject_aliases,
    load_exact_subject_aliases,
)
from core.local_inference_adapters import InferenceValidationError, build_default_registry
from core.local_inference_runtime import InferenceQueue, InferenceRuntimeError


def _read_payload(path: str | None) -> dict[str, Any]:
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SystemExit("payload must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", required=True, dest="request_type")
    parser.add_argument("--payload-file")
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--model", default=os.environ.get("SKELETON_LOCAL_INFERENCE_DEFAULT_MODEL", "qwen2.5:1.5b"))
    parser.add_argument("--root", default=os.environ.get("SKELETON_LOCAL_INFERENCE_ROOT"))
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--private-receipt", action="store_true")
    args = parser.parse_args()
    try:
        if not args.root:
            raise InferenceRuntimeError("local_inference_root_missing", retryable=False)
        registry = build_default_registry()
        adapter = registry.get(args.request_type)
        payload = _read_payload(args.payload_file)
        if args.request_type == FAMILY_DOCUMENT_REQUEST_TYPE:
            aliases = load_exact_subject_aliases(
                os.environ.get("SKELETON_FAMILY_SUBJECT_ALIASES_FILE", "")
            )
            payload = bind_family_subject_aliases(payload, aliases)
        adapter.prompt_builder(payload)
        request_id, created = InferenceQueue(args.root).submit(
            request_type=args.request_type,
            model=args.model,
            payload=payload,
            idempotency_key=args.idempotency_key,
            max_attempts=args.max_attempts,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, InferenceValidationError, InferenceRuntimeError) as exc:
        reason = exc.reason_code if isinstance(exc, InferenceRuntimeError) else "submission_invalid"
        print(json.dumps({
            "schema": "skeleton.local_inference.submit_receipt.v1",
            "status": "BLOCKED",
            "reason": reason,
        }, sort_keys=True, separators=(",", ":")))
        return 1
    receipt: dict[str, object] = {
        "schema": "skeleton.local_inference.submit_receipt.v1",
        "status": "QUEUED" if created else "DUPLICATE",
    }
    if args.private_receipt:
        receipt["request_id"] = request_id
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
