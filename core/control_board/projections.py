from __future__ import annotations

from collections import defaultdict
from typing import Any

from core.control_board.contracts import ControlBoardSnapshot


def project_view(snapshot: ControlBoardSnapshot) -> dict[str, Any]:
    lanes: dict[str, list[dict[str, str]]] = defaultdict(list)
    for card in snapshot.kanban:
        lanes[card.lane].append(card.__dict__)

    return {
        "snapshot": snapshot,
        "metrics": [metric.__dict__ for metric in snapshot.metrics],
        "today": [item.__dict__ for item in snapshot.today],
        "kanban_lanes": [{"name": name, "cards": cards} for name, cards in lanes.items()],
        "projects": [item.__dict__ for item in snapshot.projects],
        "approvals": [item.__dict__ for item in snapshot.approvals],
        "workflows": [item.__dict__ for item in snapshot.workflows],
        "evidence": [item.__dict__ for item in snapshot.evidence],
        "health": [item.__dict__ for item in snapshot.health],
    }
