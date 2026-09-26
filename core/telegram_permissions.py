from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class TelegramGatewayError(RuntimeError):
    """Raised when the Telegram gateway refuses an unsafe or unavailable action."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class TelegramAccessMode(str, Enum):
    READ_PUBLIC = "READ_PUBLIC"
    READ_ALLOWED_PRIVATE = "READ_ALLOWED_PRIVATE"
    WRITE_BOT = "WRITE_BOT"


ALLOWED_SECRET_REFS = frozenset(
    {
        "telegram_api_id",
        "telegram_api_hash",
        "telegram_bot_token",
        "telegram_mtproto_string_session",
    }
)


@dataclass(frozen=True)
class TelegramSource:
    source_id: str
    handle: str
    access_modes: tuple[TelegramAccessMode, ...]
    peer_id: str | None = None
    title: str | None = None
    secret_refs: tuple[str, ...] = ()
    allowlisted_peer_ids: tuple[str, ...] = ()
    max_page_size: int = 100
    operator_secret_route: str = "home_edge_devices_secrets_tab_to_bitwarden"
    media_downloads_default: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TelegramSource":
        source_id = _required_text(data, "source_id")
        handle = _required_text(data, "handle")
        modes = tuple(_mode(value) for value in _string_list(data.get("access_modes")))
        if not modes:
            raise TelegramGatewayError("SOURCE_ACCESS_MODE_REQUIRED", "source must declare at least one access mode")
        refs = tuple(_string_list(data.get("secret_refs")))
        forbidden_refs = sorted(set(refs) - ALLOWED_SECRET_REFS)
        if forbidden_refs:
            raise TelegramGatewayError("SECRET_REF_NOT_ALLOWED", "Telegram source references an unsupported secret")
        route = str(data.get("operator_secret_route", "home_edge_devices_secrets_tab_to_bitwarden"))
        if route != "home_edge_devices_secrets_tab_to_bitwarden":
            raise TelegramGatewayError("SECRET_ROUTE_NOT_ALLOWED", "Telegram secrets must be provisioned via Home Edge")
        max_page_size = int(data.get("max_page_size", 100))
        if max_page_size < 1 or max_page_size > 100:
            raise TelegramGatewayError("PAGE_SIZE_OUT_OF_BOUNDS", "Telegram page size must be between 1 and 100")
        return cls(
            source_id=source_id,
            handle=handle,
            access_modes=modes,
            peer_id=_optional_text(data.get("peer_id")),
            title=_optional_text(data.get("title")),
            secret_refs=refs,
            allowlisted_peer_ids=tuple(_string_list(data.get("allowlisted_peer_ids"))),
            max_page_size=max_page_size,
            operator_secret_route=route,
            media_downloads_default=bool(data.get("media_downloads_default", False)),
        )

    def require(self, mode: TelegramAccessMode) -> None:
        if mode not in self.access_modes:
            raise TelegramGatewayError("ACCESS_MODE_NOT_ALLOWED", f"{mode.value} is not enabled for this source")

    def require_peer(self, peer_id: str) -> None:
        if TelegramAccessMode.READ_ALLOWED_PRIVATE not in self.access_modes:
            return
        if str(peer_id) not in self.allowlisted_peer_ids:
            raise TelegramGatewayError("PEER_NOT_ALLOWLISTED", "private peer is not allowlisted")

    def require_public_ref(self, source_ref: str) -> str:
        self.require(TelegramAccessMode.READ_PUBLIC)
        if source_ref not in {self.source_id, self.handle, self.peer_id}:
            raise TelegramGatewayError("SOURCE_REF_NOT_ALLOWED", "public reads require the configured handle or stable source id")
        return self.handle

    def resolve_private_peer(self, source_ref: str) -> str:
        self.require(TelegramAccessMode.READ_ALLOWED_PRIVATE)
        if source_ref in {self.source_id, self.handle, self.peer_id} and self.handle in self.allowlisted_peer_ids:
            return self.handle
        if source_ref in self.allowlisted_peer_ids:
            return source_ref
        raise TelegramGatewayError("PEER_NOT_ALLOWLISTED", "private peer is not allowlisted")

    def bounded_limit(self, requested: int | None = None) -> int:
        if requested is None:
            return self.max_page_size
        if requested < 1:
            raise TelegramGatewayError("PAGE_SIZE_OUT_OF_BOUNDS", "Telegram page size must be positive")
        return min(requested, self.max_page_size)


@dataclass(frozen=True)
class TelegramAllowlist:
    sources: dict[str, TelegramSource] = field(default_factory=dict)

    @classmethod
    def from_iterable(cls, values: Iterable[Mapping[str, Any]]) -> "TelegramAllowlist":
        sources = {source.source_id: source for source in (TelegramSource.from_mapping(value) for value in values)}
        return cls(sources=sources)

    def source(self, source_id: str) -> TelegramSource:
        try:
            return self.sources[source_id]
        except KeyError as exc:
            raise TelegramGatewayError("SOURCE_NOT_ALLOWLISTED", "Telegram source is not allowlisted") from exc

    def resolve(self, source_ref: str, *, mode: TelegramAccessMode | None = None) -> TelegramSource:
        for source in self.sources.values():
            refs = {source.source_id, source.handle}
            if source.peer_id:
                refs.add(source.peer_id)
            if source_ref in refs or source_ref in source.allowlisted_peer_ids:
                if mode is not None:
                    source.require(mode)
                return source
        raise TelegramGatewayError("SOURCE_NOT_ALLOWLISTED", "Telegram source is not allowlisted")


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TelegramGatewayError("SOURCE_FIELD_REQUIRED", f"{key} is required")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TelegramGatewayError("SOURCE_FIELD_INVALID", "expected a non-empty string")
    return value.strip()


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise TelegramGatewayError("SOURCE_FIELD_INVALID", "expected a list of non-empty strings")
    return list(value)


def _mode(value: str) -> TelegramAccessMode:
    try:
        return TelegramAccessMode(value)
    except ValueError as exc:
        raise TelegramGatewayError("ACCESS_MODE_NOT_ALLOWED", "unsupported Telegram access mode") from exc
