from __future__ import annotations

from pathlib import Path
import re

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


def test_compose_has_bounded_restrictive_runtime_controls() -> None:
    compose = yaml.safe_load((ROOT / "deploy" / "control_board" / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["control-board"]

    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp:size=16m,noexec,nosuid,nodev"]
    assert service["cpus"] == "0.50"
    assert service["mem_limit"] == "256m"
    assert service["pids_limit"] == 128
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]


def test_dockerfile_has_application_healthcheck() -> None:
    dockerfile = (ROOT / "deploy" / "control_board" / "Dockerfile").read_text(encoding="utf-8")

    assert "HEALTHCHECK" in dockerfile
    assert "127.0.0.1:8080/healthz" in dockerfile
    assert "uvicorn" in dockerfile


def test_dockerfile_pins_reviewed_base_image_and_non_root_user() -> None:
    dockerfile = (ROOT / "deploy" / "control_board" / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"^FROM python:3\.12\.13-slim-bookworm@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    assert "useradd --system --uid 10001" in dockerfile
    assert "USER controlboard:controlboard" in dockerfile


def test_docs_include_tailscale_serve_notes_without_public_exposure() -> None:
    docs = (ROOT / "docs" / "CONTROL_BOARD.md").read_text(encoding="utf-8")

    assert "tailscale serve" in docs.lower()
    assert "operator" in docs.lower()
    assert "DuckDNS" in docs
    assert "Funnel" in docs
