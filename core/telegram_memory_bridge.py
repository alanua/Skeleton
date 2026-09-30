from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Mapping

from core.telegram_permissions import TelegramGatewayError


TELEGRAM_MEMORY_PROPOSAL_SCHEMA = "skeleton.telegram_gateway.memory_proposal.v1"


@dataclass
class TelegramMemoryProposalBridge:
    proposals: list[dict[str, object]] = field(default_factory=list)

    def propose(self, *, fact: str, recommendation: str | None, provenance: Mapping[str, object]) -> dict[str, object]:
        if _looks_like_bulk_history(fact):
            raise TelegramGatewayError("RAW_BULK_INGEST_FORBIDDEN", "raw Telegram history cannot enter MemoryGateway")
        if provenance.get("kind") != "telegram_message_extract":
            raise TelegramGatewayError("PROVENANCE_REQUIRED", "Telegram memory proposals require extracted provenance")
        required = {"source_id", "peer_id_hash", "message_ref_hash", "message_id", "source_evidence_hash", "provenance_hash", "confidence"}
        if not required <= set(provenance):
            raise TelegramGatewayError("PROVENANCE_REQUIRED", "Telegram memory proposals require bounded message provenance")
        confidence = provenance.get("confidence")
        if not isinstance(confidence, (float, int)) or confidence < 0 or confidence > 1:
            raise TelegramGatewayError("PROVENANCE_REQUIRED", "Telegram memory proposal confidence must be bounded")
        proposal = {
            "schema": TELEGRAM_MEMORY_PROPOSAL_SCHEMA,
            "mode": "proposal_only",
            "fact": fact,
            "recommendation": recommendation,
            "provenance": dict(provenance),
            "direct_canonical_write": False,
            "idempotency_key": hashlib.sha256(f"{fact}|{provenance}".encode("utf-8")).hexdigest(),
        }
        self.proposals.append(proposal)
        return proposal

    def direct_canonical_write(self, *_args: object, **_kwargs: object) -> None:
        raise TelegramGatewayError("DIRECT_CANONICAL_WRITE_FORBIDDEN", "Telegram bridge is proposal-only")


def _looks_like_bulk_history(value: str) -> bool:
    return value.count("\n") > 3 or len(value) > 1200
