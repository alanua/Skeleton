from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ExecutionEvidence:
    task_id: str
    envelope_hash: str
    status: str
    executor_class: str
    risk_class: str
    privacy_class: str
    step_results: tuple[Mapping[str, Any], ...]
    assertions: tuple[Mapping[str, Any], ...]

    def full_payload(self) -> dict[str, Any]:
        return {
            "schema": "skeleton.runner.execution_evidence.v1",
            "task_id": self.task_id,
            "envelope_hash": self.envelope_hash,
            "status": self.status,
            "executor_class": self.executor_class,
            "risk_class": self.risk_class,
            "privacy_class": self.privacy_class,
            "step_results": [dict(item) for item in self.step_results],
            "assertions": [dict(item) for item in self.assertions],
        }

    def public_receipt(self) -> dict[str, Any]:
        payload = self.full_payload()
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return {
            "schema": "skeleton.runner.public_receipt.v1",
            "task_id": self.task_id,
            "envelope_hash": self.envelope_hash,
            "evidence_hash": digest,
            "status": self.status,
            "executor_class": self.executor_class,
            "risk_class": self.risk_class,
            "privacy_class": self.privacy_class,
            "step_count": len(self.step_results),
            "assertion_count": len(self.assertions),
        }
