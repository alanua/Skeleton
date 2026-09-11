from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from core.home_edge.project_mobile_api import (
    BOOTSTRAP_EMPTY_PATH,
    INSPECT_PATH,
    PREPARE_REGISTER_PATH,
    READINESS_PATH,
    REGISTER_PATH,
    STATUS_PATH,
    MobileReplayCache,
    build_signed_request,
    dispatch_mobile_project_request,
)
from core.home_edge.project_onboarding import (
    ProjectOnboardingBackend,
    ProjectOnboardingCode,
    ProjectOnboardingError,
    ProtectedHeadStatus,
    RepoIdentity,
    RepositoryKind,
    RepositorySnapshot,
)


SECRET = b"home-edge-project-api-secret-32bytes"
EMPTY_URL = "https://github.com/acme/Empty.git"
FORK_URL = "https://github.com/acme/Fork.git"
STANDARD_URL = "https://github.com/acme/Standard.git"
HEAD_SHA = "b" * 40


def backend(*, runtime_ready: bool = False) -> ProjectOnboardingBackend:
    empty = RepoIdentity("github.com", "acme", "Empty")
    fork = RepoIdentity("github.com", "acme", "Fork")
    standard = RepoIdentity("github.com", "acme", "Standard")
    upstream = RepoIdentity("github.com", "alanua", "Skeleton")
    return ProjectOnboardingBackend(
        repositories={
            empty.canonical_url: RepositorySnapshot(identity=empty, kind=RepositoryKind.EMPTY, empty=True),
            fork.canonical_url: RepositorySnapshot(identity=fork, kind=RepositoryKind.FORK, fork=True, upstream=upstream),
            standard.canonical_url: RepositorySnapshot(identity=standard, kind=RepositoryKind.STANDARD),
        },
        protected_head_status=ProtectedHeadStatus(
            status="NEEDS_APPROVAL",
            pr_number=4005,
            head_ref="runner/home-edge-runner-projects-api-v1",
            head_sha=HEAD_SHA,
        ),
        canonical_runtime_ready=runtime_ready,
    )


def request(
    app: ProjectOnboardingBackend,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str = "POST",
    cache: MobileReplayCache | None = None,
    nonce: str = "nonce-1",
    idempotency_key: str = "idem-1",
) -> tuple[int, dict[str, Any]]:
    body, headers = build_signed_request(
        secret=SECRET,
        method=method,
        path=path,
        payload=payload or {},
        timestamp=1000,
        nonce=nonce,
        idempotency_key=idempotency_key,
    )
    return dispatch_mobile_project_request(
        method=method,
        path=path,
        headers=headers,
        body=body,
        secret=SECRET,
        backend=app,
        cache=cache,
        now=1000,
    )


def contains_key(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() == needle or contains_key(item, needle) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_key(item, needle) for item in value)
    return False


@pytest.mark.parametrize(
    ("repo_url", "kind"),
    [(EMPTY_URL, "EMPTY"), (FORK_URL, "FORK"), (STANDARD_URL, "STANDARD")],
)
def test_inspect_endpoint_handles_empty_fork_and_standard_without_credentials(repo_url: str, kind: str) -> None:
    status, payload = request(backend(), INSPECT_PATH, {"repo_url": repo_url})
    assert status == 200
    assert payload["code"] == "OK"
    assert payload["repository"]["kind"] == kind
    assert payload["repository"]["identity"]["host"] == "github.com"
    assert payload["inspection"]["source"] == "inspect"
    assert not contains_key(payload, "token")
    assert not contains_key(payload, "stderr")


def test_inspect_backend_has_zero_mutation() -> None:
    app = backend()
    before = deepcopy(app.__dict__)
    app.inspect(STANDARD_URL)
    assert app.__dict__ == before


def test_empty_bootstrap_requires_explicit_action_and_exact_inspected_identity() -> None:
    app = backend()
    _status, inspected = request(app, INSPECT_PATH, {"repo_url": EMPTY_URL})

    wrong = deepcopy(inspected["repository"]["identity"])
    wrong["name"] = "Standard"
    status, payload = request(
        app,
        BOOTSTRAP_EMPTY_PATH,
        {
            "repo_url": EMPTY_URL,
            "action": "bootstrap_empty_repository",
            "inspected_identity": wrong,
            "inspection": inspected["inspection"],
        },
        nonce="nonce-2",
        idempotency_key="idem-2",
    )
    assert status == 409
    assert payload["code"] == "CONFLICT"

    status, payload = request(
        app,
        BOOTSTRAP_EMPTY_PATH,
        {
            "repo_url": EMPTY_URL,
            "action": "bootstrap_empty_repository",
            "inspected_identity": inspected["repository"]["identity"],
            "inspection": inspected["inspection"],
        },
        nonce="nonce-3",
        idempotency_key="idem-3",
    )
    assert status == 200
    assert payload["bootstrap"]["status"] == "BOOTSTRAPPED"


def test_empty_bootstrap_cannot_execute_for_uninspected_identity() -> None:
    app = backend()
    identity = RepoIdentity("github.com", "acme", "Empty").to_public_mapping()
    status, payload = request(
        app,
        BOOTSTRAP_EMPTY_PATH,
        {
            "repo_url": EMPTY_URL,
            "action": "bootstrap_empty_repository",
            "inspected_identity": identity,
            "inspection": {"source": "inspect", "identity_sha256": "0" * 64},
        },
    )
    assert status == 409
    assert payload["code"] == "CONFLICT"


def test_fork_upstream_mutation_attempt_rejected_by_backend_and_bridge() -> None:
    app = backend()
    with pytest.raises(ProjectOnboardingError) as exc:
        app.prepare_register(repo_url=FORK_URL, mutate_upstream=True)
    assert exc.value.code == ProjectOnboardingCode.ACCESS_DENIED

    status, payload = request(app, PREPARE_REGISTER_PATH, {"repo_url": FORK_URL, "mutate_upstream": True})
    assert status == 403
    assert payload["code"] == "ACCESS_DENIED"


def test_protected_registration_reports_needs_approval_with_exact_head_fixture() -> None:
    status, payload = request(backend(), REGISTER_PATH, {"repo_url": STANDARD_URL})
    assert status == 200
    assert payload["code"] == "APPROVAL_REQUIRED"
    assert payload["registration"]["status"] == "NEEDS_APPROVAL"
    assert payload["protected"] == {
        "status": "NEEDS_APPROVAL",
        "pr_number": 4005,
        "head_ref": "runner/home-edge-runner-projects-api-v1",
        "head_sha": HEAD_SHA,
        "base_ref": "main",
    }


def test_duplicate_exact_repo_returns_already_registered_after_canonical_runtime_active() -> None:
    app = backend(runtime_ready=True)
    first_status, first = request(
        app,
        REGISTER_PATH,
        {"repo_url": STANDARD_URL, "project_tree_approved": True},
        nonce="nonce-1",
        idempotency_key="idem-1",
    )
    second_status, second = request(
        app,
        REGISTER_PATH,
        {"repo_url": "https://github.com/acme/Standard"},
        nonce="nonce-2",
        idempotency_key="idem-2",
    )
    assert first_status == 200
    assert first["registration"]["status"] == "ACTIVE"
    assert second_status == 200
    assert second["code"] == "ALREADY_REGISTERED"


def test_empty_register_requires_bootstrap_before_project_tree_approval() -> None:
    status, payload = request(backend(), REGISTER_PATH, {"repo_url": EMPTY_URL, "project_tree_approved": True})
    assert status == 409
    assert payload["code"] == "BOOTSTRAP_REQUIRED"


def test_private_response_contains_no_token_path_or_stderr_fields() -> None:
    status, payload = request(
        backend(),
        INSPECT_PATH,
        {
            "repo_url": STANDARD_URL,
            "github_token": "ghp_private",
            "local_path": "/home/user/private/repo",
            "stderr": "private failure",
        },
    )
    assert status == 403
    assert payload["code"] == "ACCESS_DENIED"
    assert not contains_key(payload, "token")
    assert not contains_key(payload, "path")
    assert not contains_key(payload, "stderr")


def test_malformed_url_readiness_status_and_idempotency_conflict_are_public_safe() -> None:
    app = backend()
    status, payload = request(app, INSPECT_PATH, {"repo_url": "ssh://github.com/acme/repo"})
    assert status == 400
    assert payload["code"] == "MALFORMED_URL"

    ready_status, ready = request(app, READINESS_PATH, {}, method="GET", nonce="nonce-2", idempotency_key="idem-2")
    assert ready_status == 200
    assert ready["readiness"]["status"] == "PENDING_CANONICAL_RUNTIME"

    status_status, status_payload = request(app, STATUS_PATH, {}, method="GET", nonce="nonce-3", idempotency_key="idem-3")
    assert status_status == 200
    assert status_payload["status"]["read_only"] is True

    cache = MobileReplayCache()
    assert request(app, INSPECT_PATH, {"repo_url": STANDARD_URL}, cache=cache)[0] == 200
    conflict_status, conflict = request(
        app,
        INSPECT_PATH,
        {"repo_url": FORK_URL},
        cache=cache,
        nonce="nonce-4",
        idempotency_key="idem-1",
    )
    assert conflict_status == 409
    assert conflict["code"] == "CONFLICT"
