from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol


MAIL_MEDIA_PROVIDER_UPDATE_SCHEMA = "skeleton.mail.media_provider_update.v1"
MEDIA_PROVIDER_UPDATE_CANDIDATE_KIND = "REFRESH_TRIGGER_NOT_RELEASE_PROOF"
TARGETED_REFRESH_INTENT_SCHEMA = "skeleton.home_media.targeted_refresh_intent.v1"
WAITING_DEPENDENCY_SCHEMA = "skeleton.mail.media_provider_update.waiting_dependency.v1"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_MAX_TEXT = 100_000
_MAX_PATTERN = 2_000
_MAX_LINKS = 64
_DEFAULT_DEDUPE_WINDOW_SECONDS = 7 * 86400


class MailMediaProviderUpdateError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class CanonicalMediaWorkResolver(Protocol):
    def resolve_provider_work(
        self, *, provider_adapter_ref: str, provider_work_ref: str
    ) -> "CanonicalMediaWorkResolution":
        """Resolve through the existing canonical media-work identity authority."""


class HomeMediaRefreshSink(Protocol):
    def emit_targeted_refresh(self, intent: "TargetedRefreshIntent") -> "RefreshSinkResult":
        """Emit a bounded refresh request without mutating media state."""


class MailMediaProviderUpdateStore(Protocol):
    def get(self, key: str) -> Mapping[str, Any] | None:
        pass

    def put_if_absent(self, key: str, value: Mapping[str, Any]) -> bool:
        pass


@dataclass(frozen=True)
class MailMediaProviderUpdateInput:
    mail_record_ref: str
    account_ref: str
    received_at: int
    sender_evidence: str
    subject: str
    body: str
    links: tuple[str, ...]

    def __post_init__(self) -> None:
        _safe_token(self.mail_record_ref, "mail_record_ref")
        _safe_token(self.account_ref, "account_ref")
        _non_negative_int(self.received_at, "received_at")
        _bounded_text(self.sender_evidence, "sender_evidence")
        _bounded_text(self.subject, "subject")
        _bounded_text(self.body, "body")
        if not isinstance(self.links, tuple) or len(self.links) > _MAX_LINKS:
            raise MailMediaProviderUpdateError(
                "INVALID_LINKS", "links must be a bounded tuple"
            )
        for link in self.links:
            _bounded_text(link, "link")


@dataclass(frozen=True)
class ProviderLinkPattern:
    pattern: str
    provider_work_ref_group: str = "work_ref"

    def __post_init__(self) -> None:
        _pattern(self.pattern, "link_pattern")
        _safe_token(self.provider_work_ref_group, "provider_work_ref_group")
        try:
            compiled = re.compile(self.pattern)
        except re.error as exc:
            raise MailMediaProviderUpdateError(
                "INVALID_LINK_PATTERN", "link pattern is invalid"
            ) from exc
        if self.provider_work_ref_group not in compiled.groupindex:
            raise MailMediaProviderUpdateError(
                "LINK_PATTERN_MISSING_WORK_REF",
                "link pattern must expose a named provider work ref group",
            )


@dataclass(frozen=True)
class MediaProviderNoticeAdapter:
    adapter_ref: str
    account_ref: str
    sender_evidence_patterns: tuple[str, ...]
    link_patterns: tuple[ProviderLinkPattern, ...]
    subject_patterns: tuple[str, ...] = ()
    body_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _safe_token(self.adapter_ref, "adapter_ref")
        _safe_token(self.account_ref, "account_ref")
        if not self.sender_evidence_patterns:
            raise MailMediaProviderUpdateError(
                "MISSING_SENDER_PATTERN", "sender evidence patterns are required"
            )
        if not self.link_patterns:
            raise MailMediaProviderUpdateError(
                "MISSING_LINK_PATTERN", "link patterns are required"
            )
        object.__setattr__(
            self,
            "sender_evidence_patterns",
            _compiled_pattern_sources(
                self.sender_evidence_patterns, "sender_evidence_pattern"
            ),
        )
        object.__setattr__(
            self,
            "subject_patterns",
            _compiled_pattern_sources(self.subject_patterns, "subject_pattern"),
        )
        object.__setattr__(
            self,
            "body_patterns",
            _compiled_pattern_sources(self.body_patterns, "body_pattern"),
        )


@dataclass(frozen=True)
class MediaProviderUpdateCandidate:
    kind: str
    provider_adapter_ref: str
    provider_work_ref: str
    private_mail_record_ref: str
    private_account_ref: str
    received_at: int

    def __post_init__(self) -> None:
        if self.kind != MEDIA_PROVIDER_UPDATE_CANDIDATE_KIND:
            raise MailMediaProviderUpdateError(
                "INVALID_CANDIDATE_KIND", "candidate kind is not a release proof"
            )
        _safe_token(self.provider_adapter_ref, "provider_adapter_ref")
        _safe_token(self.provider_work_ref, "provider_work_ref")
        _safe_token(self.private_mail_record_ref, "private_mail_record_ref")
        _safe_token(self.private_account_ref, "private_account_ref")
        _non_negative_int(self.received_at, "received_at")

    @property
    def private_candidate_ref(self) -> str:
        return _opaque_ref(
            "mail-media-candidate",
            {
                "kind": self.kind,
                "provider_adapter_ref": self.provider_adapter_ref,
                "provider_work_ref": self.provider_work_ref,
                "mail_record_ref": self.private_mail_record_ref,
                "account_ref": self.private_account_ref,
            },
        )


@dataclass(frozen=True)
class CanonicalMediaWorkResolution:
    status: str
    canonical_work_ref: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"RESOLVED", "UNRESOLVED", "IGNORED"}:
            raise MailMediaProviderUpdateError(
                "INVALID_RESOLUTION_STATUS", "resolution status is invalid"
            )
        if self.status == "RESOLVED":
            if self.canonical_work_ref is None:
                raise MailMediaProviderUpdateError(
                    "MISSING_CANONICAL_WORK_REF", "resolved work requires a canonical ref"
                )
            _safe_token(self.canonical_work_ref, "canonical_work_ref")
        elif self.canonical_work_ref is not None:
            raise MailMediaProviderUpdateError(
                "UNEXPECTED_CANONICAL_WORK_REF",
                "unresolved work must not include a canonical ref",
            )
        if self.reason_code is not None:
            _safe_token(self.reason_code, "reason_code")


@dataclass(frozen=True)
class TargetedRefreshIntent:
    canonical_work_ref: str
    reason_code: str
    candidate_kind: str = MEDIA_PROVIDER_UPDATE_CANDIDATE_KIND

    def __post_init__(self) -> None:
        _safe_token(self.canonical_work_ref, "canonical_work_ref")
        _safe_token(self.reason_code, "reason_code")
        if self.candidate_kind != MEDIA_PROVIDER_UPDATE_CANDIDATE_KIND:
            raise MailMediaProviderUpdateError(
                "INVALID_REFRESH_REASON_KIND", "refresh reason kind is invalid"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": TARGETED_REFRESH_INTENT_SCHEMA,
            "canonical_work_ref": self.canonical_work_ref,
            "reason_code": self.reason_code,
            "candidate_kind": self.candidate_kind,
            "mutates_release_or_play_state": False,
        }


@dataclass(frozen=True)
class RefreshSinkResult:
    status: str
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"EMITTED", "WAITING_DEPENDENCY"}:
            raise MailMediaProviderUpdateError(
                "INVALID_SINK_STATUS", "sink status is invalid"
            )
        if self.reason_code is not None:
            _safe_token(self.reason_code, "reason_code")


@dataclass(frozen=True)
class MailMediaProviderUpdateReceipt:
    status: str
    reason_code: str
    candidate_kind: str = MEDIA_PROVIDER_UPDATE_CANDIDATE_KIND
    candidate_count: int = 0
    refresh_intent_count: int = 0
    ignored_count: int = 0
    deduped_count: int = 0
    waiting_dependency_count: int = 0
    public_candidate_refs: tuple[str, ...] = ()
    public_canonical_work_refs: tuple[str, ...] = ()
    waiting_dependency_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"DONE", "IGNORED", "WAITING_DEPENDENCY"}:
            raise MailMediaProviderUpdateError(
                "INVALID_RECEIPT_STATUS", "receipt status is invalid"
            )
        _safe_token(self.reason_code, "reason_code")
        if self.candidate_kind != MEDIA_PROVIDER_UPDATE_CANDIDATE_KIND:
            raise MailMediaProviderUpdateError(
                "INVALID_RECEIPT_KIND", "receipt candidate kind is invalid"
            )
        for value in (
            self.candidate_count,
            self.refresh_intent_count,
            self.ignored_count,
            self.deduped_count,
            self.waiting_dependency_count,
        ):
            _non_negative_int(value, "count")
        for ref in (
            self.public_candidate_refs
            + self.public_canonical_work_refs
            + self.waiting_dependency_refs
        ):
            _safe_token(ref, "public_ref")

    def public_receipt(self) -> dict[str, Any]:
        return {
            "schema": MAIL_MEDIA_PROVIDER_UPDATE_SCHEMA,
            "status": self.status,
            "reason_code": self.reason_code,
            "candidate_kind": self.candidate_kind,
            "candidate_count": self.candidate_count,
            "refresh_intent_count": self.refresh_intent_count,
            "ignored_count": self.ignored_count,
            "deduped_count": self.deduped_count,
            "waiting_dependency_count": self.waiting_dependency_count,
            "public_candidate_refs": list(self.public_candidate_refs),
            "public_canonical_work_refs": list(self.public_canonical_work_refs),
            "waiting_dependency_refs": list(self.waiting_dependency_refs),
            "privacy_boundary": "PUBLIC_SAFE_AGGREGATE_ONLY",
            "private_fields_included": False,
            "provider_identifiers_included": False,
            "mail_body_or_url_included": False,
            "external_mail_reads_executed": False,
            "home_media_mutations_executed": False,
            "release_proof": False,
            "released": None,
            "playable": None,
            "translated": None,
            "watched": None,
            "active": None,
        }


class InMemoryMailMediaProviderUpdateStore:
    def __init__(self) -> None:
        self._records: dict[str, Mapping[str, Any]] = {}

    def get(self, key: str) -> Mapping[str, Any] | None:
        return self._records.get(key)

    def put_if_absent(self, key: str, value: Mapping[str, Any]) -> bool:
        if key in self._records:
            return False
        self._records[key] = MappingProxyType(dict(value))
        return True


class JsonMailMediaProviderUpdateStore:
    """Small local durable idempotency store for replay and dependency waits."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def get(self, key: str) -> Mapping[str, Any] | None:
        return self._load().get(key)

    def put_if_absent(self, key: str, value: Mapping[str, Any]) -> bool:
        records = self._load()
        if key in records:
            return False
        records[key] = dict(value)
        self._save(records)
        return True

    def _load(self) -> dict[str, Any]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise MailMediaProviderUpdateError(
                "INVALID_STORE", "idempotency store is malformed"
            )
        return raw

    def _save(self, records: Mapping[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(records, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(self.path)


def process_mail_media_provider_update(
    mail: MailMediaProviderUpdateInput,
    *,
    adapters: Iterable[MediaProviderNoticeAdapter],
    resolver: CanonicalMediaWorkResolver,
    store: MailMediaProviderUpdateStore,
    sink: HomeMediaRefreshSink | None = None,
    now: int,
    dedupe_window_seconds: int = _DEFAULT_DEDUPE_WINDOW_SECONDS,
) -> MailMediaProviderUpdateReceipt:
    _non_negative_int(now, "now")
    _positive_int(dedupe_window_seconds, "dedupe_window_seconds")
    candidates = parse_media_provider_update_candidates(mail, adapters=adapters)
    if not candidates:
        return MailMediaProviderUpdateReceipt(
            status="IGNORED", reason_code="NO_PROVIDER_UPDATE_CANDIDATE", ignored_count=1
        )

    counts: Counter[str] = Counter()
    public_candidate_refs: list[str] = []
    public_canonical_refs: list[str] = []
    waiting_refs: list[str] = []
    terminal_status = "DONE"
    terminal_reason = "REFRESH_INTENT_EMITTED"

    for candidate in candidates:
        counts["candidate_count"] += 1
        public_candidate_refs.append(candidate.private_candidate_ref)
        resolution = resolver.resolve_provider_work(
            provider_adapter_ref=candidate.provider_adapter_ref,
            provider_work_ref=candidate.provider_work_ref,
        )
        if resolution.status != "RESOLVED":
            counts["ignored_count"] += 1
            terminal_reason = resolution.reason_code or "CANONICAL_WORK_UNRESOLVED"
            continue

        assert resolution.canonical_work_ref is not None
        public_canonical_ref = _opaque_ref(
            "canonical-media-work", resolution.canonical_work_ref
        )
        public_canonical_refs.append(public_canonical_ref)
        idempotency_key = _dedupe_key(
            candidate,
            canonical_work_ref=resolution.canonical_work_ref,
            dedupe_window_seconds=dedupe_window_seconds,
        )
        existing = store.get(idempotency_key)
        if existing is not None:
            counts["deduped_count"] += 1
            if existing.get("status") == "WAITING_DEPENDENCY":
                counts["waiting_dependency_count"] += 1
                waiting_refs.append(str(existing["waiting_dependency_ref"]))
                terminal_status = "WAITING_DEPENDENCY"
                terminal_reason = "HOME_MEDIA_REFRESH_SINK_UNAVAILABLE"
            continue

        intent = TargetedRefreshIntent(
            canonical_work_ref=resolution.canonical_work_ref,
            reason_code="MAIL_PROVIDER_UPDATE_NOTICE",
        )
        if sink is None:
            waiting_ref = _opaque_ref(
                "mail-media-waiting-dependency",
                {
                    "idempotency_key": idempotency_key,
                    "canonical_work_ref": resolution.canonical_work_ref,
                },
            )
            store.put_if_absent(
                idempotency_key,
                {
                    "schema": WAITING_DEPENDENCY_SCHEMA,
                    "status": "WAITING_DEPENDENCY",
                    "reason_code": "HOME_MEDIA_REFRESH_SINK_UNAVAILABLE",
                    "waiting_dependency_ref": waiting_ref,
                    "intent": intent.to_mapping(),
                },
            )
            counts["waiting_dependency_count"] += 1
            waiting_refs.append(waiting_ref)
            terminal_status = "WAITING_DEPENDENCY"
            terminal_reason = "HOME_MEDIA_REFRESH_SINK_UNAVAILABLE"
            continue

        result = sink.emit_targeted_refresh(intent)
        if result.status == "WAITING_DEPENDENCY":
            waiting_ref = _opaque_ref(
                "mail-media-waiting-dependency",
                {
                    "idempotency_key": idempotency_key,
                    "canonical_work_ref": resolution.canonical_work_ref,
                    "reason_code": result.reason_code,
                },
            )
            store.put_if_absent(
                idempotency_key,
                {
                    "schema": WAITING_DEPENDENCY_SCHEMA,
                    "status": "WAITING_DEPENDENCY",
                    "reason_code": result.reason_code
                    or "HOME_MEDIA_REFRESH_SINK_UNAVAILABLE",
                    "waiting_dependency_ref": waiting_ref,
                    "intent": intent.to_mapping(),
                },
            )
            counts["waiting_dependency_count"] += 1
            waiting_refs.append(waiting_ref)
            terminal_status = "WAITING_DEPENDENCY"
            terminal_reason = result.reason_code or "HOME_MEDIA_REFRESH_SINK_UNAVAILABLE"
            continue

        if store.put_if_absent(
            idempotency_key,
            {
                "schema": TARGETED_REFRESH_INTENT_SCHEMA,
                "status": "EMITTED",
                "intent_ref": _opaque_ref("mail-media-refresh", idempotency_key),
                "intent": intent.to_mapping(),
            },
        ):
            counts["refresh_intent_count"] += 1
        else:
            counts["deduped_count"] += 1

    if counts["refresh_intent_count"] == 0 and counts["waiting_dependency_count"] == 0:
        terminal_status = "IGNORED"
        terminal_reason = terminal_reason or "NO_REFRESH_INTENT"
    return MailMediaProviderUpdateReceipt(
        status=terminal_status,
        reason_code=terminal_reason,
        candidate_count=counts["candidate_count"],
        refresh_intent_count=counts["refresh_intent_count"],
        ignored_count=counts["ignored_count"],
        deduped_count=counts["deduped_count"],
        waiting_dependency_count=counts["waiting_dependency_count"],
        public_candidate_refs=tuple(sorted(set(public_candidate_refs))),
        public_canonical_work_refs=tuple(sorted(set(public_canonical_refs))),
        waiting_dependency_refs=tuple(sorted(set(waiting_refs))),
    )


def parse_media_provider_update_candidates(
    mail: MailMediaProviderUpdateInput,
    *,
    adapters: Iterable[MediaProviderNoticeAdapter],
) -> tuple[MediaProviderUpdateCandidate, ...]:
    candidates: dict[tuple[str, str], MediaProviderUpdateCandidate] = {}
    for adapter in adapters:
        if adapter.account_ref != mail.account_ref:
            continue
        if not _any_pattern_matches(adapter.sender_evidence_patterns, mail.sender_evidence):
            continue
        if adapter.subject_patterns and not _all_patterns_match(
            adapter.subject_patterns, mail.subject
        ):
            continue
        if adapter.body_patterns and not _all_patterns_match(adapter.body_patterns, mail.body):
            continue
        for link in mail.links:
            for link_pattern in adapter.link_patterns:
                match = re.search(link_pattern.pattern, link)
                if match is None:
                    continue
                provider_work_ref = match.group(link_pattern.provider_work_ref_group)
                if not _SAFE_TOKEN_RE.fullmatch(provider_work_ref):
                    continue
                key = (adapter.adapter_ref, provider_work_ref)
                candidates[key] = MediaProviderUpdateCandidate(
                    kind=MEDIA_PROVIDER_UPDATE_CANDIDATE_KIND,
                    provider_adapter_ref=adapter.adapter_ref,
                    provider_work_ref=provider_work_ref,
                    private_mail_record_ref=mail.mail_record_ref,
                    private_account_ref=mail.account_ref,
                    received_at=mail.received_at,
                )
    return tuple(candidates[key] for key in sorted(candidates))


def _dedupe_key(
    candidate: MediaProviderUpdateCandidate,
    *,
    canonical_work_ref: str,
    dedupe_window_seconds: int,
) -> str:
    window = candidate.received_at // dedupe_window_seconds
    return _opaque_ref(
        "mail-media-idempotency",
        {
            "kind": candidate.kind,
            "provider_adapter_ref": candidate.provider_adapter_ref,
            "canonical_work_ref": canonical_work_ref,
            "window": window,
        },
    )


def _opaque_ref(prefix: str, value: object) -> str:
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"{prefix}:{digest[:32]}"


def _any_pattern_matches(patterns: tuple[str, ...], value: str) -> bool:
    return any(re.search(pattern, value) is not None for pattern in patterns)


def _all_patterns_match(patterns: tuple[str, ...], value: str) -> bool:
    return all(re.search(pattern, value) is not None for pattern in patterns)


def _compiled_pattern_sources(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise MailMediaProviderUpdateError(
            "INVALID_PATTERN_SET", f"{field} must be a tuple"
        )
    for value in values:
        _pattern(value, field)
        try:
            re.compile(value)
        except re.error as exc:
            raise MailMediaProviderUpdateError(
                "INVALID_PATTERN", f"{field} is invalid"
            ) from exc
    return values


def _pattern(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_PATTERN:
        raise MailMediaProviderUpdateError(
            "INVALID_PATTERN", f"{field} must be a bounded string"
        )
    return value


def _bounded_text(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) > _MAX_TEXT:
        raise MailMediaProviderUpdateError(
            "INVALID_TEXT", f"{field} must be bounded text"
        )
    return value


def _safe_token(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise MailMediaProviderUpdateError(
            "INVALID_TOKEN", f"{field} must be an opaque safe token"
        )
    return value


def _non_negative_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MailMediaProviderUpdateError(
            "INVALID_INTEGER", f"{field} must be non-negative"
        )
    return value


def _positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MailMediaProviderUpdateError(
            "INVALID_INTEGER", f"{field} must be positive"
        )
    return value
