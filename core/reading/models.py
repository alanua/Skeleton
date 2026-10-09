from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Final


READING_GATEWAY_VERSION: Final = "1"
READING_GATEWAY_SCHEMA: Final = "skeleton.reading_gateway.receipt.v1"
WORK_IDENTITY_SCHEMA: Final = "skeleton.reading_gateway.work_identity.v1"
EDITION_SCHEMA: Final = "skeleton.reading_gateway.edition.v1"
READING_SESSION_SCHEMA: Final = "skeleton.reading_gateway.session.v1"
PROGRESS_CHECKPOINT_SCHEMA: Final = "skeleton.reading_gateway.progress_checkpoint.v1"

UNKNOWN: Final = "UNKNOWN"
UNAVAILABLE: Final = "UNAVAILABLE"
KNOWN: Final = "KNOWN"
IDENTITY_STATES: Final = frozenset({KNOWN, UNKNOWN, UNAVAILABLE})
SESSION_STATUSES: Final = frozenset({"ACTIVE", "CLOSED", "ABANDONED"})
FRONTENDS: Final = frozenset(
    {"MOON_READER", "SMART_AUDIOBOOK_PLAYER", "SYNTHETIC", UNKNOWN, UNAVAILABLE}
)
PROGRESS_KINDS: Final = frozenset(
    {"EBOOK_PAGE_PERCENT", "AUDIO_TIME_CHAPTER", UNKNOWN, UNAVAILABLE}
)
EBOOK_FORMATS: Final = frozenset({"EPUB", "PDF", "MOBI", "AZW3", "CBZ"})
AUDIOBOOK_FORMATS: Final = frozenset({"AUDIOBOOK"})
_SAFE_TOKEN_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_REF_PREFIX_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ReadingContractError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ReadingFormat(str, Enum):
    EPUB = "EPUB"
    PDF = "PDF"
    MOBI = "MOBI"
    AZW3 = "AZW3"
    CBZ = "CBZ"
    AUDIOBOOK = "AUDIOBOOK"
    UNKNOWN = UNKNOWN
    UNAVAILABLE = UNAVAILABLE


@dataclass(frozen=True)
class WorkIdentity:
    work_ref: str
    source_namespace: str
    source_work_hash: str | None
    identity_state: str = KNOWN

    def __post_init__(self) -> None:
        _safe_ref(self.work_ref, "work_ref")
        _safe_token(self.source_namespace, "source_namespace")
        _enum(self.identity_state, IDENTITY_STATES, "identity_state")
        if self.identity_state == KNOWN:
            _sha256(self.source_work_hash, "source_work_hash")
        elif self.source_work_hash is not None:
            raise ReadingContractError(
                "UNKNOWN_IDENTITY_HAS_HASH",
                "unknown or unavailable work identity must not carry a source hash",
            )

    @classmethod
    def new(cls, *, source_namespace: str, source_work_key: str) -> "WorkIdentity":
        _safe_token(source_namespace, "source_namespace")
        source_hash = private_identifier_hash(source_work_key)
        return cls(
            work_ref=stable_reading_ref("work", source_namespace, source_hash),
            source_namespace=source_namespace,
            source_work_hash=source_hash,
        )

    @classmethod
    def unresolved(cls, *, source_namespace: str, identity_state: str) -> "WorkIdentity":
        _enum(identity_state, frozenset({UNKNOWN, UNAVAILABLE}), "identity_state")
        return cls(
            work_ref=stable_reading_ref("work", source_namespace, identity_state),
            source_namespace=source_namespace,
            source_work_hash=None,
            identity_state=identity_state,
        )

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "schema": WORK_IDENTITY_SCHEMA,
            "work_ref": self.work_ref,
            "source_namespace": self.source_namespace,
            "identity_state": self.identity_state,
            "source_work_hash": self.source_work_hash,
            "private_identifiers_included": False,
        }


@dataclass(frozen=True)
class Edition:
    edition_ref: str
    work_ref: str
    reading_format: ReadingFormat
    edition_hash: str | None
    edition_state: str = KNOWN

    def __post_init__(self) -> None:
        _safe_ref(self.edition_ref, "edition_ref")
        _safe_ref(self.work_ref, "work_ref")
        object.__setattr__(self, "reading_format", _reading_format(self.reading_format))
        _enum(self.edition_state, IDENTITY_STATES, "edition_state")
        if self.edition_state == KNOWN:
            _sha256(self.edition_hash, "edition_hash")
            if self.reading_format in {ReadingFormat.UNKNOWN, ReadingFormat.UNAVAILABLE}:
                raise ReadingContractError(
                    "KNOWN_EDITION_UNKNOWN_FORMAT",
                    "known edition must name a concrete format",
                )
        elif self.edition_hash is not None:
            raise ReadingContractError(
                "UNKNOWN_EDITION_HAS_HASH",
                "unknown or unavailable edition must not carry an edition hash",
            )

    @classmethod
    def new(
        cls,
        *,
        work_identity: WorkIdentity,
        reading_format: ReadingFormat | str,
        edition_key: str,
    ) -> "Edition":
        normalized_format = _reading_format(reading_format)
        edition_hash = private_identifier_hash(edition_key)
        return cls(
            edition_ref=stable_reading_ref(
                "edition", work_identity.work_ref, normalized_format.value, edition_hash
            ),
            work_ref=work_identity.work_ref,
            reading_format=normalized_format,
            edition_hash=edition_hash,
        )

    @classmethod
    def unresolved(
        cls,
        *,
        work_identity: WorkIdentity,
        reading_format: ReadingFormat | str,
        edition_state: str,
    ) -> "Edition":
        _enum(edition_state, frozenset({UNKNOWN, UNAVAILABLE}), "edition_state")
        normalized_format = _reading_format(reading_format)
        return cls(
            edition_ref=stable_reading_ref(
                "edition", work_identity.work_ref, normalized_format.value, edition_state
            ),
            work_ref=work_identity.work_ref,
            reading_format=normalized_format,
            edition_hash=None,
            edition_state=edition_state,
        )

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "schema": EDITION_SCHEMA,
            "edition_ref": self.edition_ref,
            "work_ref": self.work_ref,
            "reading_format": self.reading_format.value,
            "edition_hash": self.edition_hash,
            "edition_state": self.edition_state,
            "private_identifiers_included": False,
        }


@dataclass(frozen=True)
class ReadingSession:
    session_ref: str
    edition_ref: str
    frontend: str
    started_at: int
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        _safe_ref(self.session_ref, "session_ref")
        _safe_ref(self.edition_ref, "edition_ref")
        _enum(self.frontend, FRONTENDS, "frontend")
        _non_negative_int(self.started_at, "started_at")
        _enum(self.status, SESSION_STATUSES, "status")

    @classmethod
    def new(
        cls,
        *,
        edition: Edition,
        frontend: str,
        started_at: int,
        synthetic_session_key: str = "synthetic",
    ) -> "ReadingSession":
        _non_negative_int(started_at, "started_at")
        return cls(
            session_ref=stable_reading_ref(
                "session", edition.edition_ref, frontend, started_at, synthetic_session_key
            ),
            edition_ref=edition.edition_ref,
            frontend=frontend,
            started_at=started_at,
        )

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "schema": READING_SESSION_SCHEMA,
            "session_ref": self.session_ref,
            "edition_ref": self.edition_ref,
            "frontend": self.frontend,
            "started_at": self.started_at,
            "status": self.status,
        }


@dataclass(frozen=True)
class ProgressCheckpoint:
    checkpoint_ref: str
    session_ref: str
    edition_ref: str
    reading_format: ReadingFormat
    observed_at: int
    progress_kind: str
    page_current: int | None = None
    page_total: int | None = None
    percent: float | None = None
    time_position_seconds: int | None = None
    duration_seconds: int | None = None
    chapter_ref: str | None = None

    def __post_init__(self) -> None:
        _safe_ref(self.checkpoint_ref, "checkpoint_ref")
        _safe_ref(self.session_ref, "session_ref")
        _safe_ref(self.edition_ref, "edition_ref")
        object.__setattr__(self, "reading_format", _reading_format(self.reading_format))
        _non_negative_int(self.observed_at, "observed_at")
        _enum(self.progress_kind, PROGRESS_KINDS, "progress_kind")
        if self.progress_kind == "EBOOK_PAGE_PERCENT":
            self._validate_ebook_progress()
        elif self.progress_kind == "AUDIO_TIME_CHAPTER":
            self._validate_audio_progress()
        else:
            self._validate_unresolved_progress()

    @classmethod
    def ebook(
        cls,
        *,
        session: ReadingSession,
        edition: Edition,
        observed_at: int,
        page_current: int | None = None,
        page_total: int | None = None,
        percent: float | None = None,
    ) -> "ProgressCheckpoint":
        payload = {
            "page_current": page_current,
            "page_total": page_total,
            "percent": percent,
        }
        return cls(
            checkpoint_ref=stable_reading_ref(
                "checkpoint", session.session_ref, edition.edition_ref, observed_at, "EBOOK_PAGE_PERCENT", payload
            ),
            session_ref=session.session_ref,
            edition_ref=edition.edition_ref,
            reading_format=edition.reading_format,
            observed_at=observed_at,
            progress_kind="EBOOK_PAGE_PERCENT",
            page_current=page_current,
            page_total=page_total,
            percent=percent,
        )

    @classmethod
    def audiobook(
        cls,
        *,
        session: ReadingSession,
        edition: Edition,
        observed_at: int,
        time_position_seconds: int | None = None,
        duration_seconds: int | None = None,
        chapter_ref: str | None = None,
    ) -> "ProgressCheckpoint":
        payload = {
            "time_position_seconds": time_position_seconds,
            "duration_seconds": duration_seconds,
            "chapter_ref": chapter_ref,
        }
        return cls(
            checkpoint_ref=stable_reading_ref(
                "checkpoint", session.session_ref, edition.edition_ref, observed_at, "AUDIO_TIME_CHAPTER", payload
            ),
            session_ref=session.session_ref,
            edition_ref=edition.edition_ref,
            reading_format=edition.reading_format,
            observed_at=observed_at,
            progress_kind="AUDIO_TIME_CHAPTER",
            time_position_seconds=time_position_seconds,
            duration_seconds=duration_seconds,
            chapter_ref=chapter_ref,
        )

    @classmethod
    def unresolved(
        cls,
        *,
        session: ReadingSession,
        edition: Edition,
        observed_at: int,
        progress_kind: str,
    ) -> "ProgressCheckpoint":
        _enum(progress_kind, frozenset({UNKNOWN, UNAVAILABLE}), "progress_kind")
        return cls(
            checkpoint_ref=stable_reading_ref(
                "checkpoint", session.session_ref, edition.edition_ref, observed_at, progress_kind
            ),
            session_ref=session.session_ref,
            edition_ref=edition.edition_ref,
            reading_format=edition.reading_format,
            observed_at=observed_at,
            progress_kind=progress_kind,
        )

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "schema": PROGRESS_CHECKPOINT_SCHEMA,
            "checkpoint_ref": self.checkpoint_ref,
            "session_ref": self.session_ref,
            "edition_ref": self.edition_ref,
            "reading_format": self.reading_format.value,
            "observed_at": self.observed_at,
            "progress_kind": self.progress_kind,
            "page_current": self.page_current,
            "page_total": self.page_total,
            "percent": self.percent,
            "time_position_seconds": self.time_position_seconds,
            "duration_seconds": self.duration_seconds,
            "chapter_ref": self.chapter_ref,
            "private_identifiers_included": False,
        }

    def deterministic_hash(self) -> str:
        return _sha256_text(self.to_public_mapping())

    def _validate_ebook_progress(self) -> None:
        if self.reading_format.value not in EBOOK_FORMATS:
            raise ReadingContractError(
                "EBOOK_PROGRESS_REQUIRES_EBOOK_FORMAT",
                "ebook progress requires an ebook format",
            )
        if self.time_position_seconds is not None or self.duration_seconds is not None or self.chapter_ref is not None:
            raise ReadingContractError(
                "EBOOK_AUDIO_FIELDS_PRESENT",
                "ebook progress must not include audio time or chapter fields",
            )
        if self.page_current is None and self.percent is None:
            raise ReadingContractError(
                "EBOOK_PROGRESS_EMPTY",
                "ebook progress must include page or percent progress",
            )
        if self.page_current is not None:
            _non_negative_int(self.page_current, "page_current")
        if self.page_total is not None:
            _positive_int(self.page_total, "page_total")
            if self.page_current is not None and self.page_current > self.page_total:
                raise ReadingContractError(
                    "PAGE_CURRENT_AFTER_TOTAL",
                    "page_current cannot exceed page_total",
                )
        if self.percent is not None:
            _percent(self.percent, "percent")

    def _validate_audio_progress(self) -> None:
        if self.reading_format.value not in AUDIOBOOK_FORMATS:
            raise ReadingContractError(
                "AUDIO_PROGRESS_REQUIRES_AUDIOBOOK_FORMAT",
                "audio progress requires audiobook format",
            )
        if self.page_current is not None or self.page_total is not None or self.percent is not None:
            raise ReadingContractError(
                "AUDIO_EBOOK_FIELDS_PRESENT",
                "audio progress must not include page or percent fields",
            )
        if self.time_position_seconds is None and self.chapter_ref is None:
            raise ReadingContractError(
                "AUDIO_PROGRESS_EMPTY",
                "audio progress must include time or chapter progress",
            )
        if self.time_position_seconds is not None:
            _non_negative_int(self.time_position_seconds, "time_position_seconds")
        if self.duration_seconds is not None:
            _positive_int(self.duration_seconds, "duration_seconds")
            if self.time_position_seconds is not None and self.time_position_seconds > self.duration_seconds:
                raise ReadingContractError(
                    "TIME_POSITION_AFTER_DURATION",
                    "time_position_seconds cannot exceed duration_seconds",
                )
        if self.chapter_ref is not None:
            _safe_token(self.chapter_ref, "chapter_ref")

    def _validate_unresolved_progress(self) -> None:
        if any(
            value is not None
            for value in (
                self.page_current,
                self.page_total,
                self.percent,
                self.time_position_seconds,
                self.duration_seconds,
                self.chapter_ref,
            )
        ):
            raise ReadingContractError(
                "UNRESOLVED_PROGRESS_HAS_POSITION",
                "unknown or unavailable progress must not carry position fields",
            )


@dataclass(frozen=True)
class ReadingReceipt:
    work_identity: WorkIdentity
    edition: Edition
    session: ReadingSession
    checkpoints: tuple[ProgressCheckpoint, ...]

    def __post_init__(self) -> None:
        if self.edition.work_ref != self.work_identity.work_ref:
            raise ReadingContractError("WORK_EDITION_MISMATCH", "edition must belong to work")
        if self.session.edition_ref != self.edition.edition_ref:
            raise ReadingContractError("SESSION_EDITION_MISMATCH", "session must belong to edition")
        reconciled = reconcile_checkpoints(self.checkpoints)
        for checkpoint in reconciled:
            if checkpoint.session_ref != self.session.session_ref:
                raise ReadingContractError("CHECKPOINT_SESSION_MISMATCH", "checkpoint must belong to session")
            if checkpoint.edition_ref != self.edition.edition_ref:
                raise ReadingContractError("CHECKPOINT_EDITION_MISMATCH", "checkpoint must belong to edition")
            if checkpoint.observed_at < self.session.started_at:
                raise ReadingContractError("CHECKPOINT_BEFORE_SESSION", "checkpoint observed before session start")
        object.__setattr__(self, "checkpoints", reconciled)

    @property
    def receipt_ref(self) -> str:
        return stable_reading_ref(
            "receipt",
            self.work_identity.work_ref,
            self.edition.edition_ref,
            self.session.session_ref,
            [checkpoint.checkpoint_ref for checkpoint in self.checkpoints],
        )

    def to_public_mapping(self) -> dict[str, Any]:
        latest = self.checkpoints[-1].to_public_mapping() if self.checkpoints else None
        return {
            "schema": READING_GATEWAY_SCHEMA,
            "contract_version": READING_GATEWAY_VERSION,
            "receipt_ref": self.receipt_ref,
            "privacy_boundary": "PRIVATE_READING_STATE_LOCAL_PUBLIC_SAFE_SYNTHETIC_TESTS",
            "work_identity": self.work_identity.to_public_mapping(),
            "edition": self.edition.to_public_mapping(),
            "session": self.session.to_public_mapping(),
            "checkpoint_count": len(self.checkpoints),
            "latest_checkpoint": latest,
            "checkpoints": [checkpoint.to_public_mapping() for checkpoint in self.checkpoints],
            "private_identifiers_included": False,
            "android_storage_paths_included": False,
            "live_device_interactions_included": False,
        }

    def deterministic_hash(self) -> str:
        return _sha256_text(self.to_public_mapping())


Receipt = ReadingReceipt


def reconcile_checkpoints(
    checkpoints: Iterable[ProgressCheckpoint],
) -> tuple[ProgressCheckpoint, ...]:
    by_ref: dict[str, ProgressCheckpoint] = {}
    hashes: dict[str, str] = {}
    for checkpoint in checkpoints:
        existing_hash = hashes.get(checkpoint.checkpoint_ref)
        incoming_hash = checkpoint.deterministic_hash()
        if existing_hash is not None:
            if existing_hash != incoming_hash:
                raise ReadingContractError(
                    "DUPLICATE_CHECKPOINT_CONFLICT",
                    "checkpoint_ref reused with different progress payload",
                )
            continue
        by_ref[checkpoint.checkpoint_ref] = checkpoint
        hashes[checkpoint.checkpoint_ref] = incoming_hash
    ordered = tuple(sorted(by_ref.values(), key=lambda item: (item.observed_at, item.checkpoint_ref)))
    last_seen: dict[str, int] = {}
    for checkpoint in ordered:
        previous = last_seen.get(checkpoint.session_ref)
        if previous is not None and checkpoint.observed_at < previous:
            raise ReadingContractError(
                "NON_MONOTONIC_CHECKPOINT_TIME",
                "checkpoint timestamps must be monotonic per session",
            )
        last_seen[checkpoint.session_ref] = checkpoint.observed_at
    return ordered


def stable_reading_ref(prefix: str, *parts: object) -> str:
    if not _REF_PREFIX_RE.match(prefix):
        raise ReadingContractError("INVALID_REF_PREFIX", "reference prefix is invalid")
    return f"{prefix}_{_sha256_text(parts)[:32]}"


def private_identifier_hash(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReadingContractError(
            "INVALID_PRIVATE_IDENTIFIER",
            "private identifier must be a non-empty string",
        )
    return _sha256_text(value)


def _reading_format(value: ReadingFormat | str) -> ReadingFormat:
    try:
        return value if isinstance(value, ReadingFormat) else ReadingFormat(value)
    except ValueError as exc:
        raise ReadingContractError("INVALID_READING_FORMAT", "reading_format is unknown") from exc


def _safe_token(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.match(value):
        raise ReadingContractError("INVALID_TOKEN", f"{field_name} is not a safe token")


def _safe_ref(value: str, field_name: str) -> None:
    _safe_token(value, field_name)
    if "_" not in value:
        raise ReadingContractError("INVALID_REF", f"{field_name} must be a stable reference")


def _enum(value: str, allowed: frozenset[str], field_name: str) -> None:
    if value not in allowed:
        raise ReadingContractError("INVALID_ENUM", f"{field_name} is not supported")


def _sha256(value: str | None, field_name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReadingContractError("INVALID_SHA256", f"{field_name} must be sha256 hex")


def _non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or value < 0:
        raise ReadingContractError("INVALID_NON_NEGATIVE_INT", f"{field_name} must be a non-negative integer")


def _positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ReadingContractError("INVALID_POSITIVE_INT", f"{field_name} must be a positive integer")


def _percent(value: float, field_name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 100:
        raise ReadingContractError("INVALID_PERCENT", f"{field_name} must be between 0 and 100")


def _sha256_text(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
