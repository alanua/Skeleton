from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class VideoArtifactStoreError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_REASON_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_PRIVATE_MARKERS = ("BEGIN ", "PRIVATE", "SECRET", "TRANSCRIPT", "OCR_TEXT")


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    sha256: str
    byte_size: int
    private_path: Path
    provenance: Mapping[str, object]
    confidence: float
    replay: bool

    def public_ref(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "confidence": self.confidence,
            "replay": self.replay,
        }


class PrivateVideoArtifactStore:
    def __init__(self, root: Path, *, max_artifact_bytes: int = 50_000_000) -> None:
        self.root = root.resolve(strict=False)
        self.max_artifact_bytes = max_artifact_bytes
        if isinstance(max_artifact_bytes, bool) or max_artifact_bytes <= 0:
            raise VideoArtifactStoreError("ARTIFACT_LIMIT_INVALID", "artifact byte limit is invalid")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def put_bytes(
        self,
        *,
        kind: str,
        payload: bytes,
        provenance: Mapping[str, object],
        confidence: float,
    ) -> StoredArtifact:
        if _TOKEN_RE.fullmatch(kind) is None:
            raise VideoArtifactStoreError("ARTIFACT_KIND_INVALID", "artifact kind is invalid")
        if not isinstance(payload, bytes) or not payload:
            raise VideoArtifactStoreError("ARTIFACT_EMPTY", "artifact payload is empty")
        if len(payload) > self.max_artifact_bytes:
            raise VideoArtifactStoreError("ARTIFACT_TOO_LARGE", "artifact exceeds configured bound")
        if isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
            raise VideoArtifactStoreError("ARTIFACT_CONFIDENCE_INVALID", "artifact confidence is invalid")
        encoded_provenance = json.dumps(provenance, allow_nan=False, sort_keys=True)
        digest = hashlib.sha256(payload).hexdigest()
        artifact_id = f"{kind}:{digest[:32]}"
        shard = self.root / kind / digest[:2]
        target = shard / f"{digest}.bin"
        meta = shard / f"{digest}.json"
        shard.mkdir(parents=True, exist_ok=True, mode=0o700)
        replay = target.exists()
        if not replay:
            _atomic_write(target, payload)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise VideoArtifactStoreError("ARTIFACT_HASH_MISMATCH", "artifact readback hash mismatch")
        metadata = {
            "schema": "skeleton.video_understanding.private_artifact.v1",
            "artifact_id": artifact_id,
            "sha256": digest,
            "byte_size": len(payload),
            "provenance": json.loads(encoded_provenance),
            "confidence": float(confidence),
        }
        _atomic_write(meta, json.dumps(metadata, allow_nan=False, sort_keys=True).encode("utf-8"))
        return StoredArtifact(artifact_id, digest, len(payload), target, provenance, float(confidence), replay)

    def readback(self, artifact: StoredArtifact) -> StoredArtifact:
        if not artifact.private_path.is_file():
            raise VideoArtifactStoreError("ARTIFACT_MISSING", "artifact is missing")
        if hashlib.sha256(artifact.private_path.read_bytes()).hexdigest() != artifact.sha256:
            raise VideoArtifactStoreError("ARTIFACT_HASH_MISMATCH", "artifact readback hash mismatch")
        return artifact


def public_receipt(
    *,
    operation: str,
    status: str,
    reason_code: str,
    counts: Mapping[str, int],
    review_required: bool,
    canonical_mutation_status: str,
) -> dict[str, object]:
    if _TOKEN_RE.fullmatch(operation) is None or _TOKEN_RE.fullmatch(status) is None:
        raise VideoArtifactStoreError("PUBLIC_RECEIPT_INVALID", "public receipt status is invalid")
    if _REASON_RE.fullmatch(reason_code) is None:
        raise VideoArtifactStoreError("PUBLIC_RECEIPT_INVALID", "public receipt reason code is invalid")
    receipt = {
        "schema": "skeleton.video_understanding.receipt.v1",
        "operation": operation,
        "status": status,
        "reason_code": reason_code,
        "frame_count": int(counts.get("frame_count", 0)),
        "audio_count": int(counts.get("audio_count", 0)),
        "ocr_count": int(counts.get("ocr_count", 0)),
        "transcript_count": int(counts.get("transcript_count", 0)),
        "model_output_count": int(counts.get("model_output_count", 0)),
        "artifact_count": int(counts.get("artifact_count", 0)),
        "review_required": bool(review_required),
        "canonical_mutation_status": canonical_mutation_status,
    }
    reject_private_public_receipt(receipt)
    return receipt


def reject_private_public_receipt(receipt: Mapping[str, object]) -> None:
    for value in _string_values(receipt):
        if "/" in value or "\\" in value:
            raise VideoArtifactStoreError("PUBLIC_RECEIPT_PRIVATE_DATA", "public receipt contains private-like data")
        upper = value.upper()
        for marker in _PRIVATE_MARKERS:
            if marker in upper:
                raise VideoArtifactStoreError("PUBLIC_RECEIPT_PRIVATE_DATA", "public receipt contains private-like data")


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_string_values(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_string_values(item))
        return strings
    return []


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".part")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except FileExistsError:
        pass
    finally:
        temporary.unlink(missing_ok=True)
