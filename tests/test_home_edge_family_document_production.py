from __future__ import annotations

from pathlib import Path

import pytest

import core.home_edge.family_document_production as production


def test_config_from_args_binds_runtime_paths_without_private_values() -> None:
    config = production.config_from_args(
        inbox="/var/lib/skeleton/family-document/inbox",
        archive="/var/lib/skeleton/family-document/archive",
        outbox_db="/var/lib/skeleton/family-document/outbox/receipts.sqlite3",
        env={
            "SKELETON_SCHEDULER_DB": "/var/lib/skeleton/scheduler/scheduler.sqlite3",
            "SKELETON_FAMILY_SUBJECT_ALIASES_FILE": "/etc/skeleton/family-aliases.json",
        },
    )

    assert config.inbox == Path("/var/lib/skeleton/family-document/inbox")
    assert config.archive == Path("/var/lib/skeleton/family-document/archive")
    assert config.outbox_db == Path("/var/lib/skeleton/family-document/outbox/receipts.sqlite3")
    assert config.scheduler_db == Path("/var/lib/skeleton/scheduler/scheduler.sqlite3")
    assert config.aliases_file == Path("/etc/skeleton/family-aliases.json")


def test_production_runtime_preflights_ocr_before_gateway(monkeypatch, tmp_path) -> None:
    called = []

    def fail_preflight(*, suffixes):
        called.append(tuple(suffixes))
        raise production.LocalDocumentOcrError("missing synthetic dependency")

    monkeypatch.setattr(production, "require_local_ocr_dependencies", fail_preflight)
    monkeypatch.setattr(production, "_canonical_gateway", lambda: pytest.fail("gateway should not be opened"))
    config = production.FamilyDocumentProductionConfig(
        inbox=tmp_path / "inbox",
        archive=tmp_path / "archive",
        outbox_db=tmp_path / "outbox.sqlite3",
        scheduler_db=tmp_path / "scheduler.sqlite3",
    )

    with pytest.raises(production.LocalDocumentOcrError):
        production.build_family_document_production_runtime(config)

    assert called


def test_service_unit_uses_immutable_runtime_and_no_activation_command() -> None:
    unit = Path("ops/systemd/skeleton-family-document-intake.service").read_text(encoding="utf-8")

    assert "WorkingDirectory=/opt/skeleton/current" in unit
    assert "/home/agent/" not in unit
    assert "ProtectSystem=strict" in unit
    assert "systemctl enable" not in unit
    assert "systemctl start" not in unit


def test_installer_does_not_enable_or_start_service() -> None:
    installer = Path("scripts/install_family_document_worker.sh").read_text(encoding="utf-8")

    assert "systemctl daemon-reload" in installer
    assert "systemctl enable" not in installer
    assert "systemctl start" not in installer
    assert "SKELETON_PRIVATE_MEMORY_ROOT=/var/lib/skeleton/private-memory" in installer
