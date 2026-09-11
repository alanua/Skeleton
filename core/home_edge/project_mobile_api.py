from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from core.home_edge.project_onboarding import (
    ProjectOnboardingBackend,
    ProjectOnboardingCode,
    ProjectOnboardingError,
    redact_public_metadata,
)


API_VERSION = "skeleton.home_edge.mobile.project_api.v1"
MAX_BODY_BYTES = 32768
MAX_CLOCK_SKEW_SECONDS = 300

INSPECT_PATH = "/v1/projects/inspect"
BOOTSTRAP_EMPTY_PATH = "/v1/projects/bootstrap-empty"
PREPARE_REGISTER_PATH = "/v1/projects/prepare-register"
REGISTER_PATH = "/v1/projects/register"
READINESS_PATH = "/v1/projects/readiness"
STATUS_PATH = "/v1/projects/status"


class MobileProjectApiError(ValueError):
    def __init__(self, code: ProjectOnboardingCode, message: str | None = None, *, status: int = 400) -> None:
        super().__init__(message or code.value)
        self.code = code
        self.status = status


@dataclass
class MobileReplayCache:
    seen: dict[str, tuple[str, float]] = field(default_factory=dict)

    def check(self, *, idempotency_key: str, body_sha256: str, now: float) -> None:
        cutoff = now - MAX_CLOCK_SKEW_SECONDS
        self.seen = {key: value for key, value in self.seen.items() if value[1] >= cutoff}
        existing = self.seen.get(idempotency_key)
        if existing is not None and existing[0] != body_sha256:
            raise MobileProjectApiError(ProjectOnboardingCode.CONFLICT, status=409)
        self.seen[idempotency_key] = (body_sha256, now)


def canonical_body(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_signature_text(
    *, method: str, path: str, timestamp: str, nonce: str, idempotency_key: str, body_sha256: str
) -> bytes:
    return "\n".join([API_VERSION, method.upper(), path, timestamp, nonce, idempotency_key, body_sha256]).encode("utf-8")


def sign(secret: bytes, **parts: str) -> str:
    return hmac.new(secret, canonical_signature_text(**parts), hashlib.sha256).hexdigest()


def build_signed_request(
    *,
    secret: bytes,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    timestamp: int | None = None,
    nonce: str = "nonce",
    idempotency_key: str = "idem",
) -> tuple[bytes, dict[str, str]]:
    body = canonical_body(payload or {})
    ts = str(int(time.time() if timestamp is None else timestamp))
    digest = body_hash(body)
    headers = {
        "content-type": "application/json",
        "x-home-edge-project-api-version": API_VERSION,
        "x-home-edge-timestamp": ts,
        "x-home-edge-nonce": nonce,
        "idempotency-key": idempotency_key,
    }
    headers["x-home-edge-signature"] = sign(
        secret,
        method=method,
        path=path,
        timestamp=ts,
        nonce=nonce,
        idempotency_key=idempotency_key,
        body_sha256=digest,
    )
    return body, headers


def dispatch_mobile_project_request(
    *,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
    secret: bytes,
    backend: ProjectOnboardingBackend,
    cache: MobileReplayCache | None = None,
    now: float | None = None,
) -> tuple[int, dict[str, Any]]:
    cache = cache or MobileReplayCache()
    try:
        payload = parse_signed_mobile_request(
            method=method,
            path=path,
            headers=headers,
            body=body,
            secret=secret,
            cache=cache,
            now=now,
        )
        response = _dispatch_backend(method=method.upper(), path=path, payload=payload, backend=backend)
        return 200, _mobile_success_response(response)
    except MobileProjectApiError as exc:
        return exc.status, _mobile_error_response(exc.code)
    except ProjectOnboardingError as exc:
        return exc.status, _mobile_error_response(exc.code)


def parse_signed_mobile_request(
    *,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
    secret: bytes,
    cache: MobileReplayCache,
    now: float | None = None,
) -> dict[str, Any]:
    if len(body) > MAX_BODY_BYTES:
        raise MobileProjectApiError(ProjectOnboardingCode.CONFLICT, status=413)
    normalized_headers = {str(key).lower(): str(value) for key, value in headers.items()}
    if normalized_headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise MobileProjectApiError(ProjectOnboardingCode.CONFLICT, status=415)
    timestamp = normalized_headers.get("x-home-edge-timestamp", "")
    nonce = normalized_headers.get("x-home-edge-nonce", "")
    idempotency_key = normalized_headers.get("idempotency-key", "")
    signature = normalized_headers.get("x-home-edge-signature", "")
    version = normalized_headers.get("x-home-edge-project-api-version", "")
    if version != API_VERSION or not all((timestamp, nonce, idempotency_key, signature)):
        raise MobileProjectApiError(ProjectOnboardingCode.ACCESS_DENIED, status=401)
    current = time.time() if now is None else now
    try:
        request_time = int(timestamp)
    except ValueError as exc:
        raise MobileProjectApiError(ProjectOnboardingCode.ACCESS_DENIED, status=401) from exc
    if abs(current - request_time) > MAX_CLOCK_SKEW_SECONDS:
        raise MobileProjectApiError(ProjectOnboardingCode.ACCESS_DENIED, status=401)
    digest = body_hash(body)
    expected = sign(
        secret,
        method=method.upper(),
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        idempotency_key=idempotency_key,
        body_sha256=digest,
    )
    if not hmac.compare_digest(signature, expected):
        raise MobileProjectApiError(ProjectOnboardingCode.ACCESS_DENIED, status=401)
    cache.check(idempotency_key=idempotency_key, body_sha256=digest, now=current)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MobileProjectApiError(ProjectOnboardingCode.CONFLICT) from exc
    if not isinstance(payload, dict):
        raise MobileProjectApiError(ProjectOnboardingCode.CONFLICT)
    _reject_credentials(payload)
    return payload


def _dispatch_backend(
    *, method: str, path: str, payload: Mapping[str, Any], backend: ProjectOnboardingBackend
) -> dict[str, Any]:
    if method == "POST" and path == INSPECT_PATH:
        return backend.inspect(str(payload.get("repo_url", "")))
    if method == "POST" and path == BOOTSTRAP_EMPTY_PATH:
        inspected = payload.get("inspected_identity", {})
        if not isinstance(inspected, Mapping):
            raise MobileProjectApiError(ProjectOnboardingCode.CONFLICT, status=409)
        return backend.bootstrap_empty(
            repo_url=str(payload.get("repo_url", "")),
            inspected_identity=inspected,
            inspection=_mapping(payload.get("inspection", {})),
            action=str(payload.get("action", "")),
        )
    if method == "POST" and path == PREPARE_REGISTER_PATH:
        return backend.prepare_register(
            repo_url=str(payload.get("repo_url", "")),
            mutate_upstream=bool(payload.get("mutate_upstream", False)),
            project_tree_approved=bool(payload.get("project_tree_approved", False)),
        )
    if method == "POST" and path == REGISTER_PATH:
        return backend.register(
            repo_url=str(payload.get("repo_url", "")),
            mutate_upstream=bool(payload.get("mutate_upstream", False)),
            project_tree_approved=bool(payload.get("project_tree_approved", False)),
        )
    if method == "GET" and path == READINESS_PATH:
        return backend.readiness()
    if method == "GET" and path == STATUS_PATH:
        repo_url = payload.get("repo_url")
        return backend.status(str(repo_url) if repo_url else None)
    raise MobileProjectApiError(ProjectOnboardingCode.CONFLICT, status=404)


def _reject_credentials(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lower = str(key).lower()
            if any(token in lower for token in ("token", "secret", "credential", "password", "authorization")):
                raise MobileProjectApiError(ProjectOnboardingCode.ACCESS_DENIED, status=403)
            _reject_credentials(item)
    elif isinstance(value, list):
        for item in value:
            _reject_credentials(item)


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MobileProjectApiError(ProjectOnboardingCode.CONFLICT, status=409)
    return value


def _mobile_error_response(code: ProjectOnboardingCode) -> dict[str, Any]:
    return {
        "schema": f"{API_VERSION}.response",
        "action": "error",
        "code": code.value,
        "error": {"code": code.value},
    }


def _mobile_success_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    response = dict(payload)
    backend_schema = response.get("schema")
    response["schema"] = f"{API_VERSION}.response"
    if backend_schema:
        response["backend_schema"] = str(backend_schema)
    return redact_public_metadata(response)
