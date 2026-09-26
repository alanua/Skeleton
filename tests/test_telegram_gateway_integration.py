from __future__ import annotations

from pathlib import Path

from core.telegram_gateway import TelegramGateway
from core.telegram_permissions import TelegramAllowlist, TelegramSource
from core.telegram_store import TelegramStore


def test_midnightquantum_harness_is_bounded_and_auth_aware(tmp_path: Path) -> None:
    source = TelegramSource.from_mapping(
        {
            "source_id": "midnight",
            "handle": "@midnightquantum",
            "access_modes": ["READ_ALLOWED_PRIVATE"],
            "secret_refs": ["telegram_api_id", "telegram_api_hash"],
            "allowlisted_peer_ids": ["@midnightquantum"],
            "operator_secret_route": "home_edge_devices_secrets_tab_to_bitwarden",
        }
    )
    gateway = TelegramGateway(allowlist=TelegramAllowlist({"midnight": source}), store=TelegramStore.open(tmp_path / "telegram.db"))

    assert gateway.run_public_integration_harness("midnight") == {"status": "AUTH_REQUIRED", "source": "@midnightquantum"}


def test_midnightquantum_harness_does_not_attempt_live_login(tmp_path: Path) -> None:
    source = TelegramSource.from_mapping(
        {
            "source_id": "midnight",
            "handle": "@midnightquantum",
            "access_modes": ["READ_ALLOWED_PRIVATE"],
            "secret_refs": ["telegram_api_id", "telegram_api_hash", "telegram_mtproto_string_session"],
            "allowlisted_peer_ids": ["@midnightquantum"],
            "operator_secret_route": "home_edge_devices_secrets_tab_to_bitwarden",
        }
    )
    gateway = TelegramGateway(allowlist=TelegramAllowlist({"midnight": source}), store=TelegramStore.open(tmp_path / "telegram.db"))

    assert gateway.run_public_integration_harness("midnight") == {
        "status": "BLOCKED",
        "reason_code": "LIVE_AUTHORIZATION_REQUIRED",
        "source": "@midnightquantum",
    }
