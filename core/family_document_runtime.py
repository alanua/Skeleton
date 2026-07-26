from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


FAMILY_RUNTIME_SCHEMA = "skeleton.family_document_runtime_config.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(dict(value), sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.chmod(mode)
    temp.replace(path)
    path.chmod(mode)


def atomic_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_bytes(data)
    temp.chmod(mode)
    temp.replace(path)
    path.chmod(mode)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("runtime_record_not_object")
    return raw


@dataclass(frozen=True)
class FamilyDocumentRuntime:
    root: Path

    @classmethod
    def open(cls, root: str | Path) -> "FamilyDocumentRuntime":
        resolved = Path(root).expanduser().resolve(strict=False)
        for child in (
            "claims",
            "sessions",
            "records",
            "assemblies",
            "receipts",
            "handoff",
            "state",
        ):
            (resolved / child).mkdir(parents=True, exist_ok=True)
        try:
            resolved.chmod(0o700)
        except OSError:
            pass
        return cls(resolved)

    def sequence_path(self) -> Path:
        return self.root / "state" / "sequence.json"

    def next_sequence(self) -> int:
        path = self.sequence_path()
        state = read_json(path) or {"next_sequence": 1}
        value = int(state.get("next_sequence", 1))
        atomic_json(path, {"next_sequence": value + 1})
        return value

    def session_path(self, session_id: str) -> Path:
        return self.root / "sessions" / f"{session_id}.json"

    def claim_path(self, component_id: str) -> Path:
        return self.root / "claims" / f"{component_id}.json"

    def record_path(self, document_id: str) -> Path:
        return self.root / "records" / f"{document_id}.json"

    def receipt_path(self, receipt_id: str) -> Path:
        return self.root / "receipts" / f"{receipt_id}.json"

    def assembly_path(self, document_id: str) -> Path:
        return self.root / "assemblies" / f"{document_id}.pdf"

    def open_sessions(self) -> list[dict[str, Any]]:
        sessions = []
        for path in sorted((self.root / "sessions").glob("*.json")):
            data = read_json(path)
            if data and data.get("state") == "open":
                sessions.append(data)
        return sessions

    def write_session(self, session: Mapping[str, Any]) -> None:
        atomic_json(self.session_path(str(session["session_id"])), session)

    def write_claim(self, component_id: str, claim: Mapping[str, Any]) -> None:
        atomic_json(self.claim_path(component_id), claim)

    def write_record(self, document_id: str, record: Mapping[str, Any]) -> None:
        atomic_json(self.record_path(document_id), record)

    def write_receipt(self, receipt_id: str, receipt: Mapping[str, Any]) -> None:
        atomic_json(self.receipt_path(receipt_id), receipt, mode=0o644)

    def component_id_for_path(self, path: str | Path) -> str:
        source = Path(path).expanduser().resolve(strict=False)
        data = source.read_bytes()
        return sha256_bytes(str(source).encode("utf-8") + b"\0" + sha256_bytes(data).encode("ascii"))


def private_repair_handoff(
    *,
    runtime: FamilyDocumentRuntime,
    repair_id: str,
    component_record_ids: list[str],
    supersedes_document_ids: list[str],
    merged_document_id: str,
) -> dict[str, object]:
    handoff = {
        "schema": "skeleton.family_document_private_repair_handoff.v1",
        "repair_id": repair_id,
        "action": "create_merged_logical_document_without_deletion",
        "component_record_ids": component_record_ids,
        "supersedes_document_ids": supersedes_document_ids,
        "merged_document_id": merged_document_id,
        "relations": ["component_of", "supersedes"],
        "delete_original_records": False,
        "private_runtime_only": True,
        "created_at": utc_now(),
    }
    atomic_json(runtime.root / "handoff" / f"{repair_id}.json", handoff)
    return handoff
