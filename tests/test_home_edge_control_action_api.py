from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from scripts import home_edge_control_action_api as action_api

API_KEY = "a" * 48


def _request(url: str, *, method: str = "GET", key: str | None = None, payload: dict | None = None):
    headers = {}
    data = None
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = urllib.request.Request(url, method=method, headers=headers, data=data)
    return urllib.request.urlopen(request, timeout=3)


@pytest.fixture
def action_server(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(action_api.API_KEY_ENV, API_KEY)
    monkeypatch.setattr(
        action_api,
        "_execute_media",
        lambda **_kwargs: {
            "schema": "skeleton.home_media.result.v1",
            "status": "ok",
            "action": "status",
            "selected_mode": "chrome",
            "active_mode_hint": "chrome",
            "volume_percent": 100,
            "muted": False,
            "available_modes": ["android_tv", "chrome", "kiosk", "off", "vlc"],
            "receipt_hash": "status-hash",
            "duration_seconds": 0.4,
        },
    )
    monkeypatch.setattr(
        action_api,
        "_handle_control",
        lambda args: {
            "schema": "skeleton.home_media.result.v1",
            "status": "ok",
            "action": "control",
            "requested_mode": args.get("mode"),
            "selected_mode": args.get("mode"),
            "active_mode_hint": args.get("mode", "chrome"),
            "volume_percent": args.get("volume_percent", 100),
            "muted": False,
            "available_modes": ["android_tv", "chrome", "kiosk", "off", "vlc"],
            "receipt_hash": "control-hash",
            "duration_seconds": 0.8,
        },
    )
    action_api._REQUEST_TIMES.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), action_api.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_health_and_openapi_are_public_but_contain_no_private_values(action_server: str) -> None:
    with _request(action_server + "/health") as response:
        assert json.load(response) == {"service": "skeleton-home-media-action", "status": "ok"}
    with _request(action_server + "/openapi.json") as response:
        document = json.load(response)
    encoded = json.dumps(document, sort_keys=True)
    assert document["components"]["securitySchemes"]["bearerAuth"] == {"type": "http", "scheme": "bearer"}
    schema = document["components"]["schemas"]["MediaControl"]
    assert schema["properties"]["mode"]["enum"] == ["chrome", "android_tv", "vlc", "kiosk", "off"]
    for forbidden in ("argv", "script", "environment", "ssh", "signature", "hmac"):
        assert forbidden not in encoded.lower()


def test_status_requires_bearer_key(action_server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        _request(action_server + "/v1/media/status")
    assert exc_info.value.code == 401

    with _request(action_server + "/v1/media/status", key=API_KEY) as response:
        payload = json.load(response)
    assert payload["status"] == "ok"
    assert payload["selected_mode"] == "chrome"


def test_control_accepts_only_bounded_payload(action_server: str) -> None:
    with _request(
        action_server + "/v1/media/control",
        method="POST",
        key=API_KEY,
        payload={"mode": "android_tv", "volume_percent": 80},
    ) as response:
        payload = json.load(response)
    assert payload["status"] == "ok"
    assert payload["selected_mode"] == "android_tv"
    assert payload["volume_percent"] == 80


def test_api_key_must_be_private_and_long(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(action_api.API_KEY_ENV, "short")
    with pytest.raises(RuntimeError):
        action_api._api_key()


def test_openapi_server_url_is_optional_and_bounded() -> None:
    without_url = action_api._openapi_document()
    assert "servers" not in without_url
    with_url = action_api._openapi_document("https://media.example.ts.net/")
    assert with_url["servers"] == [{"url": "https://media.example.ts.net"}]
