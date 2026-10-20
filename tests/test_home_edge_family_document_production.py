from __future__ import annotations

from pathlib import Path

import pytest

from core.home_edge import family_document_production as production
from core.local_document_ocr import LocalDocumentOcrError


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install_family_document_worker.sh"
SERVICE = ROOT / "ops/systemd/skeleton-family-document-intake.service"


def test_family_document_worker_service_runs_unprivileged_with_strict_writes() -> None:
    service = SERVICE.read_text(encoding="utf-8")

    assert "User=agent" in service
    assert "Group=agent" in service
    assert "NoNewPrivileges=true" in service
    assert "PrivateTmp=true" in service
    assert "ProtectSystem=strict" in service
    assert "MemoryDenyWriteExecute=true" in service
    assert "ReadWritePaths=/var/lib/skeleton/family-documents/archive /var/lib/skeleton/family-documents/outbox /var/lib/skeleton/scheduler /home/agent/.local/share/skeleton-private-memory" in service
    assert "ReadWritePaths=/var/lib/skeleton " not in service
    assert "WorkingDirectory=/home/agent/agent-dev/Skeleton" not in service


def test_family_document_installer_creates_private_agent_dirs_and_stays_disabled() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert 'run_as_user="${2:-agent}"' in installer
    assert '"$run_as_user" == "agent"' in installer
    assert "-o \"$run_as_user\" -g \"$run_as_user\" -m 0700 \"$inbox_root\" \"$archive_root\" \"$outbox_root\" \"$private_memory_root\"" in installer
    assert "-o \"$run_as_user\" -g \"$run_as_user\" -m 0700 \"$scheduler_root\"" in installer
    assert "systemctl enable" not in installer
    assert "systemctl start" not in installer
    assert "INSTALLED_NOT_STARTED" in installer


def test_production_runtime_checks_ocr_dependencies_before_canonical_mutation(monkeypatch) -> None:
    def unavailable() -> None:
        raise LocalDocumentOcrError("local OCR dependencies unavailable")

    monkeypatch.setattr(production, "assert_default_local_ocr_available", unavailable)

    with pytest.raises(LocalDocumentOcrError, match="dependencies unavailable"):
        production.build_family_document_runtime(
            inbox="/var/lib/skeleton/family-documents/inbox",
            archive="/var/lib/skeleton/family-documents/archive",
            outbox_db="/var/lib/skeleton/family-documents/outbox/receipts.sqlite3",
        )
