from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Mapping

import pytest


@pytest.fixture(autouse=True)
def _model_installed_snapshot_signer_for_legacy_boundary_tests(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    """Keep legacy config-boundary tests on the installed-signer authority path.

    This fixture is deliberately scoped to the single historical snapshot test module.
    Production code is never given a direct controller-config HMAC fallback; the model
    loads the reviewed static signer payload and translates its process-style auth
    rejection into the ValueError contract observed by the Runner boundary.
    """
    if request.node.path.name != "test_home_edge_media_source_snapshot.py":
        yield
        return

    from core.home_edge import media_source_snapshot as snapshot
    from core.home_edge.executor import HomeEdgeExecRequest

    root = Path(__file__).resolve().parents[1]
    payload_path = root / "scripts/home_edge_media_source_snapshot_signer_payload.py"

    def modeled_installed_signer(unsigned: Mapping[str, Any]) -> HomeEdgeExecRequest:
        spec = importlib.util.spec_from_file_location(
            "snapshot_static_signer_legacy_boundary_model",
            payload_path,
        )
        assert spec is not None and spec.loader is not None
        payload = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(payload)

        def fail_as_boundary_error(reason: str = "snapshot_signer_rejected") -> None:
            raise ValueError(reason)

        payload.fail = fail_as_boundary_error
        secret = payload.read_secret()
        signature = payload.sign(dict(unsigned), secret)
        return HomeEdgeExecRequest.from_mapping(
            {**dict(unsigned), "signature": signature}
        )

    monkeypatch.setattr(
        snapshot,
        "_sign_snapshot_request_with_installed_signer",
        modeled_installed_signer,
    )
    yield
