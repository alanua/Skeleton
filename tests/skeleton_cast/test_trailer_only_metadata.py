from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[2]
APP = ROOT / "ops/skeleton_cast/runtime/app.py"


class _FakeFlask:
    config: dict[str, object]

    def __init__(self, _name: str) -> None:
        self.config = {}

    def get(self, _rule: str):
        return lambda func: func

    def post(self, _rule: str):
        return lambda func: func

    def after_request(self, func):
        return func


def _load_app_module(monkeypatch):
    flask = types.ModuleType("flask")
    flask.Flask = _FakeFlask
    flask.Response = object
    flask.abort = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("abort"))
    flask.jsonify = lambda value=None, **kwargs: value if kwargs == {} else {**(value or {}), **kwargs}
    flask.request = types.SimpleNamespace(remote_addr="127.0.0.1", get_json=lambda **_kwargs: {})
    flask.send_from_directory = lambda *_args, **_kwargs: object()

    player = types.ModuleType("player")
    player.status = lambda: {}
    player.mode_status = lambda: {"mode": "off"}
    player.switch_mode = lambda mode: {"mode": mode}
    player.play = lambda job, source, subtitles: {"played": True}
    player.control = lambda action: {"action": action}

    site_registry = types.ModuleType("site_registry")
    site_registry.validate_public_url = lambda url: (url, "public.example")
    site_registry.lookup = lambda host: (host, {"status": "confirmed"})
    site_registry.make_candidate = lambda page_url, result: {}
    site_registry.confirm = lambda candidate: candidate

    resolver = types.ModuleType("resolver")
    resolver.BrowserChallengeError = type("BrowserChallengeError", (Exception,), {})
    resolver.OriginProtectedError = type("OriginProtectedError", (Exception,), {})
    resolver.resolve_page = lambda page_url: {"title": "Synthetic", "sources": []}

    monkeypatch.setitem(sys.modules, "flask", flask)
    monkeypatch.setitem(sys.modules, "player", player)
    monkeypatch.setitem(sys.modules, "site_registry", site_registry)
    monkeypatch.setitem(sys.modules, "resolver", resolver)

    spec = importlib.util.spec_from_file_location("skeleton_cast_app_for_tests", APP)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _source(
    source_id: str,
    *,
    is_trailer: bool | None,
    translation: str = "Українська озвучка",
    playable: bool = True,
    url: str = "https://public.example/video.m3u8",
) -> dict[str, object]:
    source: dict[str, object] = {
        "source_id": f"public-{source_id}",
        "url": url,
        "quality": "Авто",
        "translation": translation,
        "headers": {},
        "has_drm": False,
        "playable": playable,
    }
    if is_trailer is not None:
        source["is_trailer"] = is_trailer
    return source


def _movie(*sources: dict[str, object], year: int = 2026) -> dict[str, object]:
    return {
        "job_id": "0123456789abcdef",
        "status": "ready",
        "media_type": "movie",
        "year": year,
        "title": "Synthetic public work",
        "sources": list(sources),
    }


def test_movie_with_trailer_only_shows_marker(monkeypatch):
    app = _load_app_module(monkeypatch)

    metadata = app._derive_work_card_metadata(_movie(_source("trailer-1", is_trailer=True)))

    assert metadata["trailer_only"] is True
    assert metadata["work_card_metadata_label"] == "Фільм · Лише трейлер · 2026"


def test_movie_with_multiple_trailers_only_shows_marker(monkeypatch):
    app = _load_app_module(monkeypatch)

    metadata = app._derive_work_card_metadata(
        _movie(
            _source("trailer-1", is_trailer=True),
            _source("trailer-2", is_trailer=True, url="https://public.example/trailer-2.m3u8"),
        )
    )

    assert metadata["trailer_only"] is True
    assert metadata["work_card_metadata_label"] == "Фільм · Лише трейлер · 2026"


def test_movie_with_trailer_and_translated_full_source_hides_marker(monkeypatch):
    app = _load_app_module(monkeypatch)

    metadata = app._derive_work_card_metadata(
        _movie(_source("trailer-1", is_trailer=True), _source("full-1", is_trailer=False))
    )

    assert metadata["trailer_only"] is False
    assert metadata["work_card_metadata_label"] == "Фільм · 2026"


def test_source_refresh_to_translated_full_source_removes_marker(monkeypatch):
    app = _load_app_module(monkeypatch)
    trailer_only_job = _movie(_source("trailer-1", is_trailer=True))
    refreshed_job = _movie(
        _source("trailer-1", is_trailer=True),
        _source("full-1", is_trailer=False, url="https://public.example/full.m3u8"),
    )

    assert app._derive_work_card_metadata(trailer_only_job)["trailer_only"] is True
    assert app._derive_work_card_metadata(refreshed_job)["work_card_metadata_label"] == "Фільм · 2026"


def test_full_source_without_usable_translation_keeps_trailer_only(monkeypatch):
    app = _load_app_module(monkeypatch)

    metadata = app._derive_work_card_metadata(
        _movie(
            _source("trailer-1", is_trailer=True),
            _source("full-1", is_trailer=False, translation="Озвучення не вказано"),
        )
    )

    assert metadata["trailer_only"] is True
    assert metadata["work_card_metadata_label"] == "Фільм · Лише трейлер · 2026"


def test_series_unaffected_by_trailer_marker(monkeypatch):
    app = _load_app_module(monkeypatch)
    job = {"media_type": "series", "year": 2026, "sources": [_source("trailer-1", is_trailer=True)]}

    assert app._derive_work_card_metadata(job) == {"trailer_only": False}


def test_history_restoration_and_fresh_discovery_produce_same_marker(monkeypatch):
    app = _load_app_module(monkeypatch)
    sources = [_source("trailer-1", is_trailer=True)]
    history_job = _movie(*sources)
    fresh_job = _movie(*[dict(source) for source in sources])

    assert app._derive_work_card_metadata(history_job) == app._derive_work_card_metadata(fresh_job)


def test_ambiguous_source_classification_does_not_claim_full_release(monkeypatch):
    app = _load_app_module(monkeypatch)

    metadata = app._derive_work_card_metadata(
        _movie(_source("trailer-1", is_trailer=True), _source("unknown-1", is_trailer=None))
    )

    assert metadata["trailer_only"] is True
    assert metadata["work_card_metadata_label"] == "Фільм · Лише трейлер · 2026"


def test_get_job_derives_metadata_before_public_source_sanitization(monkeypatch):
    app = _load_app_module(monkeypatch)
    job = _movie(_source("trailer-1", is_trailer=True))

    monkeypatch.setattr(app, "_require", lambda: None)
    monkeypatch.setattr(app, "_load", lambda job_id: job)

    response = app.get_job("0123456789abcdef")

    assert response["trailer_only"] is True
    assert response["work_card_metadata_label"] == "Фільм · Лише трейлер · 2026"
    assert "url" not in response["sources"][0]
    assert "public-trailer-1" in str(response)
    assert "private" not in str(response).lower()
