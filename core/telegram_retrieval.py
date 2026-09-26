from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from core.telegram_store import TelegramStore


SemanticSearch = Callable[[str, int, Mapping[str, object]], list[Mapping[str, object]]]


@dataclass(frozen=True)
class TelegramRetrieval:
    store: TelegramStore
    semantic_search: SemanticSearch | None = None

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        semantic: bool = False,
        source_id: str | None = None,
        peer_id: str | None = None,
        min_date: str | None = None,
        max_date: str | None = None,
    ) -> list[Mapping[str, object]]:
        filters = {"source_id": source_id, "peer_id": peer_id, "min_date": min_date, "max_date": max_date}
        if semantic and self.semantic_search is not None:
            return list(self.semantic_search(query, max(1, min(limit, 100)), filters))
        return self.store.search(query, limit=limit, source_id=source_id, peer_id=peer_id, min_date=min_date, max_date=max_date)
