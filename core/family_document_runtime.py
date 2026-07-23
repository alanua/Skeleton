from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class FamilyDocumentRuntimeError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass
class FileArchiveSink:
    root: Path

    def put(self, *, cluster_id: str, relative_path: Path, source: Path, expected_sha256: str, dry_run: bool) -> dict[str, object]:
        target = self.root / relative_path
        if dry_run:
            return {"status": "DRY_RUN", "path_token": _token(str(relative_path)), "verified": False}
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha256_file(target) == expected_sha256:
            return {"status": "DUPLICATE_EXISTING", "path_token": _token(str(relative_path)), "verified": True}
        fd, temp_name = tempfile.mkstemp(prefix=".family-doc-", dir=str(target.parent))
        os.close(fd)
        temp = Path(temp_name)
        try:
            shutil.copyfile(source, temp)
            if sha256_file(temp) != expected_sha256:
                raise FamilyDocumentRuntimeError("ARCHIVE_VERIFY_FAILED", "archive copy hash mismatch")
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink()
        if sha256_file(target) != expected_sha256:
            raise FamilyDocumentRuntimeError("ARCHIVE_VERIFY_FAILED", "archive read-back hash mismatch")
        return {"status": "WRITTEN", "path_token": _token(str(relative_path)), "verified": True}


@dataclass
class InMemoryGatewaySink:
    records: dict[str, Mapping[str, object]] = field(default_factory=dict)
    writes: int = 0

    def commit(self, record: Mapping[str, object]) -> dict[str, object]:
        cluster_id = str(record["cluster_id"])
        if cluster_id in self.records:
            return {"status": "DUPLICATE_EXISTING", "canonical_id": _token(cluster_id)}
        self.records[cluster_id] = dict(record)
        self.writes += 1
        return {"status": "COMMITTED", "canonical_id": _token(cluster_id)}


@dataclass
class InMemoryCalendarSink:
    events: dict[str, Mapping[str, object]] = field(default_factory=dict)
    writes: int = 0

    def upsert(self, event_id: str, payload: Mapping[str, object]) -> dict[str, object]:
        forbidden = {"attendees", "conferenceData", "hangoutLink", "sendUpdates"}
        if forbidden.intersection(payload):
            raise FamilyDocumentRuntimeError("CALENDAR_PRIVACY_VIOLATION", "calendar payload contains external notification fields")
        normalized = dict(payload)
        if self.events.get(event_id) == normalized:
            return {"status": "DUPLICATE_EXISTING", "event_token": _token(event_id)}
        self.events[event_id] = normalized
        self.writes += 1
        return {"status": "UPSERTED", "event_token": _token(event_id)}


class JsonOperationalJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

    def record(self, cluster_id: str) -> dict[str, Any]:
        return self.data.setdefault("clusters", {}).setdefault(cluster_id, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, sort_keys=True, indent=2)
        fd, temp_name = tempfile.mkstemp(prefix=".journal-", dir=str(self.path.parent))
        os.close(fd)
        temp = Path(temp_name)
        try:
            temp.write_text(payload + "\n", encoding="utf-8")
            temp.replace(self.path)
        finally:
            if temp.exists():
                temp.unlink()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": "skeleton.family_document_journal.v1", "clusters": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def public_token(value: str) -> str:
    return _token(value)


def _token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
