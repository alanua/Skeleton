from __future__ import annotations

from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from core.control_board.app import create_app_from_snapshot
from core.control_board.contracts import ControlBoardSnapshot


class HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.headings: list[str] = []
        self._capture = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2"}:
            self._capture = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2"}:
            self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture:
            self.headings.append(data.strip())


def _client_with_payload(payload: dict) -> TestClient:
    return TestClient(create_app_from_snapshot(ControlBoardSnapshot.from_mapping(payload)))


def _payload() -> dict:
    return {
        "schema": "skeleton.control_board.snapshot.v1",
        "generated_at": "2026-07-07T09:00:00Z",
        "title": "Synthetic <script>alert(1)</script>",
        "subtitle": "Public-safe dashboard",
        "metrics": [{"label": "Open", "value": "1", "tone": "ok"}],
        "today": [{"title": "Today", "detail": "<img src=x onerror=alert(1)>", "status": "review", "owner": "Runner"}],
        "kanban": [{"title": "Card", "detail": "Detail", "lane": "Ready", "status": "queued"}],
        "projects": [{"name": "Skeleton", "summary": "Summary", "status": "running", "updated": "synthetic"}],
        "approvals": [{"request": "Approve", "source": "Synthetic", "status": "review"}],
        "workflows": [{"name": "Workflow", "stage": "Stage", "status": "watch", "next_step": "Next"}],
        "evidence": [{"label": "Evidence", "reference": "Reference", "status": "queued"}],
        "health": [{"component": "Backend", "status": "ok", "detail": "Healthy"}],
    }


def test_dashboard_renders_required_sections_and_escapes_source_text() -> None:
    response = _client_with_payload(_payload()).get("/")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<img src=x onerror=alert(1)>" not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text

    parser = HeadingParser()
    parser.feed(response.text)
    for section in ["Today", "Kanban", "Projects", "Approvals", "Workflows", "Evidence", "Health"]:
        assert section in parser.headings


def test_static_assets_are_local_and_mobile_viewport_is_declared() -> None:
    client = _client_with_payload(_payload())
    response = client.get("/")

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
    assert 'href="http' not in response.text
    assert 'src="http' not in response.text
    assert client.get("/static/control-board.css").status_code == 200
    assert client.get("/static/control-board.js").status_code == 200


@pytest.mark.parametrize("path", ["/", "/healthz"])
def test_responses_include_restrictive_security_headers(path: str) -> None:
    response = _client_with_payload(_payload()).get(path)

    assert response.headers["content-security-policy"] == (
        "default-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'; "
        "img-src 'self'; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    assert response.headers["x-frame-options"] == "DENY"


def test_app_exposes_no_mutation_routes() -> None:
    client = _client_with_payload(_payload())
    assert client.post("/").status_code == 405
    assert client.put("/").status_code == 405
    assert client.delete("/").status_code == 405

    methods = {method for route in client.app.routes for method in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}
