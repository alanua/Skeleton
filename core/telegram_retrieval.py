from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from core.telegram_store import TelegramStore


SemanticSearch = Callable[[str, int], list[Mapping[str, object]]]


@dataclass(frozen=True)
class TelegramRetrieval:
    store: TelegramStore
    semantic_search: SemanticSearch | None = None

    def search(self, query: str, *, limit: int = 20, semantic: bool = False) -> list[Mapping[str, object]]:
        if semantic and self.semantic_search is not None:
            return list(self.semantic_search(query, max(1, min(limit, 100))))
        return self.store.search(query, limit=limit)
