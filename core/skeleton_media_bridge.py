from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import importlib.util
from types import ModuleType
from typing import Any, Callable, Protocol

SKELETON_MEDIA_REPOSITORY = "alanua/skeleton-media"
SKELETON_MEDIA_REQUIRED_SHA = "e4531a3aad4a830b9d3dd5b55c8eff7f444e6a5d"
CANONICAL_HOME_MEDIA_ENDPOINT_SCHEMA = "skeleton.home.media_endpoints.v1"

_EXTERNAL_PREFIX = "skeleton_media"
_SUPPORTED_MODULES = frozenset(
    {
        "home_edge.debian_media_bootstrap",
        "home_edge.media_source_snapshot",
        "video_understanding.runtime_install",
    }
)


class SkeletonMediaDependencyError(RuntimeError):
    pass


class MediaEndpointError(ValueError):
    pass


class MediaEndpointCapability:
    MODE = "mode"
    PLAYER_STATUS = "player_status"
    REMOTE_CONTROL = "remote_control"
    SEEK = "seek"
    VOLUME = "volume"
    HISTORY = "history"
    PLAY = "play"
    TV_CHANNELS = "tv_channels"
    HANDOFF = "handoff"


@dataclass(frozen=True)
class MediaEndpoint:
    endpoint_id: str
    label: str
    adapter_kind: str
    capabilities: tuple[str, ...]
    icon: str = "television"

    def __post_init__(self) -> None:
        if not _safe_endpoint_id(self.endpoint_id):
            raise MediaEndpointError("endpoint_id_invalid")
        if not self.label.strip():
            raise MediaEndpointError("endpoint_label_required")
        if not self.adapter_kind.strip():
            raise MediaEndpointError("endpoint_adapter_required")
        if not self.capabilities:
            raise MediaEndpointError("endpoint_capabilities_required")

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def to_mapping(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "label": self.label,
            "adapter_kind": self.adapter_kind,
            "capabilities": list(self.capabilities),
            "icon": self.icon,
        }


@dataclass
class EndpointState:
    mode: str = "mpv"
    player: dict[str, Any] = field(default_factory=dict)
    volume: int = 25
    last_nonzero_volume: int = 25
    history: list[dict[str, Any]] = field(default_factory=list)
    session: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "player": dict(self.player),
            "volume": self.volume,
            "last_nonzero_volume": self.last_nonzero_volume,
            "history": [dict(item) for item in self.history],
            "session": dict(self.session),
        }


@dataclass(frozen=True)
class MediaMutation:
    endpoint_id: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)


class MediaEndpointAdapter(Protocol):
    endpoint: MediaEndpoint

    def snapshot(self) -> EndpointState: ...

    def mutate(self, mutation: MediaMutation) -> EndpointState: ...


class InMemoryCapabilityAdapter:
    """Deterministic adapter used by tests and by canonical route planning."""

    def __init__(self, endpoint: MediaEndpoint, state: EndpointState | None = None) -> None:
        self.endpoint = endpoint
        self._state = state or EndpointState()
        self.mutations: list[MediaMutation] = []

    def snapshot(self) -> EndpointState:
        return _copy_state(self._state)

    def mutate(self, mutation: MediaMutation) -> EndpointState:
        if mutation.endpoint_id != self.endpoint.endpoint_id:
            raise MediaEndpointError("mutation_endpoint_mismatch")
        _require_capability(self.endpoint, mutation.action)
        payload = dict(mutation.payload)
        if mutation.action == MediaEndpointCapability.MODE:
            self._state.mode = str(payload.get("mode") or self._state.mode)
        elif mutation.action == MediaEndpointCapability.VOLUME:
            level = int(payload.get("level", self._state.volume))
            self._state.volume = max(0, min(100, level))
            if self._state.volume > 0:
                self._state.last_nonzero_volume = self._state.volume
        elif mutation.action == MediaEndpointCapability.PLAY:
            self._state.player = {
                **self._state.player,
                **payload,
                "running": True,
                "endpoint_id": self.endpoint.endpoint_id,
            }
            self._state.session = {
                "endpoint_id": self.endpoint.endpoint_id,
                "source_id": payload.get("source_id", ""),
                "job_id": payload.get("job_id", ""),
            }
        elif mutation.action == MediaEndpointCapability.REMOTE_CONTROL:
            self._state.session = {**self._state.session, "last_remote_action": payload.get("action", "")}
        elif mutation.action == MediaEndpointCapability.SEEK:
            self._state.player = {**self._state.player, "time-pos": float(payload.get("position", 0.0))}
        elif mutation.action == MediaEndpointCapability.HISTORY:
            item = dict(payload)
            item.setdefault("endpoint_id", self.endpoint.endpoint_id)
            self._state.history.append(item)
        elif mutation.action == MediaEndpointCapability.HANDOFF:
            self._state.session = {**self._state.session, "last_handoff": dict(payload)}
        self.mutations.append(mutation)
        return self.snapshot()


class HomeEdgeTvAdapter(InMemoryCapabilityAdapter):
    def __init__(self, endpoint_id: str = "home_edge_tv") -> None:
        super().__init__(home_edge_tv_endpoint(endpoint_id))


class SamsungMediaAdapter(InMemoryCapabilityAdapter):
    def __init__(self, endpoint_id: str = "samsung_tv") -> None:
        super().__init__(samsung_media_endpoint(endpoint_id))


class MediaEndpointRegistry:
    def __init__(self, adapters: list[MediaEndpointAdapter] | tuple[MediaEndpointAdapter, ...]) -> None:
        self._adapters = {adapter.endpoint.endpoint_id: adapter for adapter in adapters}
        if len(self._adapters) != len(adapters):
            raise MediaEndpointError("duplicate_endpoint_id")

    @property
    def endpoints(self) -> tuple[MediaEndpoint, ...]:
        return tuple(adapter.endpoint for adapter in self._adapters.values())

    def adapter(self, endpoint_id: str) -> MediaEndpointAdapter:
        if not _safe_endpoint_id(endpoint_id) or endpoint_id not in self._adapters:
            raise MediaEndpointError("unknown_endpoint_id")
        return self._adapters[endpoint_id]

    def snapshot(self, endpoint_id: str) -> EndpointState:
        return self.adapter(endpoint_id).snapshot()

    def mutate(self, endpoint_id: str, action: str, payload: dict[str, Any] | None = None) -> EndpointState:
        if not endpoint_id:
            raise MediaEndpointError("endpoint_id_required")
        return self.adapter(endpoint_id).mutate(
            MediaMutation(endpoint_id=endpoint_id, action=action, payload=dict(payload or {}))
        )

    def handoff(self, *, source_endpoint_id: str, destination_endpoint_id: str, move: bool = False) -> dict[str, Any]:
        if source_endpoint_id == destination_endpoint_id:
            raise MediaEndpointError("handoff_requires_distinct_endpoints")
        source = self.adapter(source_endpoint_id)
        destination = self.adapter(destination_endpoint_id)
        _require_capability(source.endpoint, MediaEndpointCapability.HANDOFF)
        _require_capability(destination.endpoint, MediaEndpointCapability.HANDOFF)
        source_state = source.snapshot()
        payload = {
            "source_endpoint_id": source_endpoint_id,
            "destination_endpoint_id": destination_endpoint_id,
            "mode": source_state.mode,
            "player": dict(source_state.player),
            "session": dict(source_state.session),
            "history": [dict(item) for item in source_state.history],
            "move": move,
        }
        destination.mutate(
            MediaMutation(
                endpoint_id=destination_endpoint_id,
                action=MediaEndpointCapability.HANDOFF,
                payload=payload,
            )
        )
        if move:
            source.mutate(
                MediaMutation(
                    endpoint_id=source_endpoint_id,
                    action=MediaEndpointCapability.HANDOFF,
                    payload={"moved_to_endpoint_id": destination_endpoint_id, "cleared": True},
                )
            )
        return payload

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": CANONICAL_HOME_MEDIA_ENDPOINT_SCHEMA,
            "endpoints": [endpoint.to_mapping() for endpoint in self.endpoints],
        }


def home_edge_tv_endpoint(endpoint_id: str = "home_edge_tv") -> MediaEndpoint:
    return MediaEndpoint(
        endpoint_id=endpoint_id,
        label="TV",
        adapter_kind="home_edge_tv",
        icon="television",
        capabilities=(
            MediaEndpointCapability.MODE,
            MediaEndpointCapability.PLAYER_STATUS,
            MediaEndpointCapability.REMOTE_CONTROL,
            MediaEndpointCapability.SEEK,
            MediaEndpointCapability.VOLUME,
            MediaEndpointCapability.HISTORY,
            MediaEndpointCapability.PLAY,
            MediaEndpointCapability.TV_CHANNELS,
            MediaEndpointCapability.HANDOFF,
        ),
    )


def samsung_media_endpoint(endpoint_id: str = "samsung_tv") -> MediaEndpoint:
    return MediaEndpoint(
        endpoint_id=endpoint_id,
        label="Samsung",
        adapter_kind="samsung",
        icon="devices",
        capabilities=(
            MediaEndpointCapability.MODE,
            MediaEndpointCapability.PLAYER_STATUS,
            MediaEndpointCapability.REMOTE_CONTROL,
            MediaEndpointCapability.SEEK,
            MediaEndpointCapability.VOLUME,
            MediaEndpointCapability.HISTORY,
            MediaEndpointCapability.PLAY,
            MediaEndpointCapability.HANDOFF,
        ),
    )


def canonical_home_media_registry(
    extra_adapters: list[MediaEndpointAdapter] | tuple[MediaEndpointAdapter, ...] = (),
) -> MediaEndpointRegistry:
    return MediaEndpointRegistry(
        (
            HomeEdgeTvAdapter(),
            SamsungMediaAdapter(),
            *tuple(extra_adapters),
        )
    )


def canonical_home_media_endpoints() -> dict[str, Any]:
    return canonical_home_media_registry().to_mapping()


def external_package_available() -> bool:
    return importlib.util.find_spec(_EXTERNAL_PREFIX) is not None


def load_media_module(relative_name: str) -> ModuleType:
    if relative_name not in _SUPPORTED_MODULES:
        raise SkeletonMediaDependencyError(
            f"unsupported_skeleton_media_module:{relative_name}"
        )
    if not external_package_available():
        raise SkeletonMediaDependencyError(
            f"skeleton_media_dependency_unavailable:{relative_name}"
        )
    module_name = f"{_EXTERNAL_PREFIX}.{relative_name}"
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        raise SkeletonMediaDependencyError(
            f"external_skeleton_media_import_failed:{relative_name}"
        ) from exc


def dependency_source(relative_name: str) -> str:
    if relative_name not in _SUPPORTED_MODULES:
        raise SkeletonMediaDependencyError(
            f"unsupported_skeleton_media_module:{relative_name}"
        )
    return "external" if external_package_available() else "unavailable"


def _safe_endpoint_id(value: str) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and all(ch.isascii() and (ch.isalnum() or ch in {"_", "-"}) for ch in value)
    )


def _copy_state(state: EndpointState) -> EndpointState:
    return EndpointState(
        mode=state.mode,
        player=dict(state.player),
        volume=state.volume,
        last_nonzero_volume=state.last_nonzero_volume,
        history=[dict(item) for item in state.history],
        session=dict(state.session),
    )


def _require_capability(endpoint: MediaEndpoint, action: str) -> None:
    if action not in endpoint.capabilities:
        raise MediaEndpointError("endpoint_capability_unsupported")



# Draft protected catalog/ingress contract for Skeleton #4788.
# This is NOT a live gateway registration or a route to system execution.
# Keep the existing 2-tool Skeleton Control MCP surface unchanged.
GLOBAL_VIDEO_PROVIDER_ID = "skeleton.video_understanding.global"
VIDEO_TRANSCRIPT_SOURCE_DEPENDENCY_PR = "alanua/skeleton-media#71"
GLOBAL_VIDEO_OPERATIONS = (
    "video_understand_url",
    "video_import_urls",
    "video_process_one",
    "video_status",
    "video_doctor",
    "video_query",
    "video_reprocess",
    "video_attach_to_project",
    "video_transcribe_url",
)
_VIDEO_TRANSCRIBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com"}
)


class GlobalVideoIngressError(ValueError):
    """A typed global video ingress request is invalid or unauthorized."""


def global_video_provider_catalog() -> dict[str, Any]:
    """Describe ONE source-only provider, never claim a live executor exists."""
    return {
        "schema": "skeleton.video_understanding.global_provider.v1",
        "provider_id": GLOBAL_VIDEO_PROVIDER_ID,
        "status": "SOURCE_ONLY_NOT_DEPLOYED",
        "runtime_registered": False,
        "live_runtime_execution": False,
        "required_external_source_pr": VIDEO_TRANSCRIPT_SOURCE_DEPENDENCY_PR,
        "external_source_sha": SKELETON_MEDIA_REQUIRED_SHA,
        "control_mcp_tools_changed": False,
        "shared_worker_policy": "ONE_EXISTING_VIDEO_WORKER",
        "shared_queue_policy": "ONE_EXISTING_VIDEO_QUEUE",
        "canonical_memory_policy": "EXISTING_MEMORY_GATEWAY_ONLY",
        "dios_policy": "PRESERVE_LEGACY",
        "operations": list(GLOBAL_VIDEO_OPERATIONS),
    }


def plan_global_video_transcribe_ingress(
    payload: dict[str, Any],
    *,
    authenticated_caller: str | None,
    trusted_gate_check: Callable[[str, str], bool] | None,
) -> dict[str, Any]:
    """Validate a proposed private plan; intentionally no executor/dispatch.

    The caller identity and Gate callable MUST be supplied by an authenticated
    host, never extracted from payload. This helper is NOT the Gate itself.
    A trusted host still must verify external package pin, registered runtime,
    unique queue authority and separate operator approval before execution.
    """
    from urllib.parse import urlsplit

    if not isinstance(payload, dict):
        raise GlobalVideoIngressError("VIDEO_INGRESS_PAYLOAD_INVALID")
    allowed = frozenset({"operation", "url", "mode", "verify_with_independent_asr"})
    if set(payload) - allowed:
        raise GlobalVideoIngressError("VIDEO_INGRESS_FIELDS_FORBIDDEN")
    if payload.get("operation") != "video_transcribe_url":
        raise GlobalVideoIngressError("VIDEO_INGRESS_OPERATION_INVALID")
    if not isinstance(authenticated_caller, str) or not (
        1 <= len(authenticated_caller) <= 128
        and all(ch.isascii() and (ch.isalnum() or ch in "-_.:") for ch in authenticated_caller)
    ):
        raise GlobalVideoIngressError("VIDEO_INGRESS_CALLER_REQUIRED")
    if not callable(trusted_gate_check):
        raise GlobalVideoIngressError("VIDEO_INGRESS_GATE_UNAVAILABLE")
    # A false result or exception fails closed without exposing caller/private data.
    try:
        permitted = trusted_gate_check(authenticated_caller, "video_transcribe_url")
    except Exception as exc:
        raise GlobalVideoIngressError("VIDEO_INGRESS_GATE_DENIED") from exc
    if permitted is not True:
        raise GlobalVideoIngressError("VIDEO_INGRESS_GATE_DENIED")
    mode = payload.get("mode", "QUICK")
    if not isinstance(mode, str) or mode not in {"QUICK", "STANDARD"}:
        raise GlobalVideoIngressError("VIDEO_INGRESS_MODE_UNSUPPORTED")
    verify = payload.get("verify_with_independent_asr", False)
    if not isinstance(verify, bool):
        raise GlobalVideoIngressError("VIDEO_INGRESS_ASR_FLAG_INVALID")
    url = payload.get("url")
    if not isinstance(url, str) or not 1 <= len(url) <= 4096 or url != url.strip():
        raise GlobalVideoIngressError("VIDEO_INGRESS_URL_INVALID")
    try:
        parsed = urlsplit(url)
        safe_url = (
            parsed.scheme == "https"
            and parsed.hostname in _VIDEO_TRANSCRIBE_HOSTS
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and bool(parsed.path and parsed.path != "/")
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in url)
        )
    except ValueError as exc:
        raise GlobalVideoIngressError("VIDEO_INGRESS_URL_INVALID") from exc
    if not safe_url:
        raise GlobalVideoIngressError("VIDEO_INGRESS_URL_INVALID")
    return {
        "private_plan": {
            "schema": "skeleton.video_understanding.transcribe_ingress_plan.v1",
            "provider_id": GLOBAL_VIDEO_PROVIDER_ID,
            "operation": "video_transcribe_url",
            "url": url,
            "mode": mode,
            "verify_with_independent_asr": verify,
            "shared_worker_policy": "ONE_EXISTING_VIDEO_WORKER",
            "runtime_registered": False,
            "execution_requested": False,
            "canonical_mutation": "NOT_USED",
            "llm_policy": "NOT_USED",
            "vision_policy": "NOT_USED",
        },
        "public_receipt": {
            "schema": "skeleton.video_understanding.transcribe_ingress_receipt.v1",
            "provider_id": GLOBAL_VIDEO_PROVIDER_ID,
            "operation": "video_transcribe_url",
            "status": "PLANNED_NOT_REGISTERED",
            "runtime_registered": False,
            "execution_performed": False,
            "canonical_mutation_status": "NOT_USED",
        },
    }
