from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from core.runner_execution_broker import default_broker
from core.runner_executors import ExecutionContext
from core.task_envelope import parse_task_envelope


class TaskEnvelopeRuntimeError(RuntimeError):
    pass


def execute_task_envelope_file(
    envelope_path: str | Path,
    *,
    context: ExecutionContext | None = None,
    evidence_dir: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(envelope_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise TaskEnvelopeRuntimeError("envelope path is not a file")
    if path.stat().st_mode & 0o077:
        raise TaskEnvelopeRuntimeError("envelope file must be private")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise TaskEnvelopeRuntimeError("envelope JSON must be an object")
    envelope = parse_task_envelope(raw)
    active_context = context or ExecutionContext(
        targets={},
        entrypoints={},
        roots={},
        environment=dict(os.environ),
    )
    result = default_broker(active_context).execute(envelope)

    if evidence_dir is not None:
        evidence_root = Path(evidence_dir).expanduser().resolve(strict=False)
        evidence_root.mkdir(parents=True, exist_ok=True)
        evidence_root.chmod(0o700)
        destination = evidence_root / f"{envelope.task_id}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                result["private_evidence"],
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, destination)
        destination.chmod(0o600)

    return result["public_receipt"]
