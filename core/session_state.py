from typing import Optional


class SessionState:
    """In-memory active project context. Never writes to disk."""

    def __init__(self) -> None:
        self._active_project: Optional[str] = None
        self._context: Optional[dict] = None

    def activate(self, project_id: str, context: dict) -> None:
        self._active_project = project_id
        self._context = context

    def deactivate(self) -> None:
        self._active_project = None
        self._context = None

    def active_project(self) -> Optional[str]:
        return self._active_project

    def context(self) -> Optional[dict]:
        return self._context

    def is_active(self) -> bool:
        return self._active_project is not None
