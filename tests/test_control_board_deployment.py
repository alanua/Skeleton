from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_compose_is_localhost_only_single_app_with_healthcheck() -> None:
    compose = yaml.safe_load((ROOT / "deploy" / "control_board" / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert list(services) == ["control-board"]
    service = services["control-board"]
    assert service["ports"] == ["127.0.0.1:8080:8080"]
    assert "healthcheck" in service
    assert "control_board" in service["build"]["dockerfile"]


def test_dockerfile_has_application_healthcheck() -> None:
    dockerfile = (ROOT / "deploy" / "control_board" / "Dockerfile").read_text(encoding="utf-8")

    assert "HEALTHCHECK" in dockerfile
    assert "127.0.0.1:8080/healthz" in dockerfile
    assert "uvicorn" in dockerfile


def test_docs_include_tailscale_serve_notes_without_public_exposure() -> None:
    docs = (ROOT / "docs" / "CONTROL_BOARD.md").read_text(encoding="utf-8")

    assert "tailscale serve" in docs.lower()
    assert "operator" in docs.lower()
    assert "DuckDNS" in docs
    assert "Funnel" in docs
