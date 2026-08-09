from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "ops/skeleton_cast/runtime/app.py"


class FakeFlask:
    def __init__(self, _name: str) -> None:
        self.routes: dict[tuple[str, str], object] = {}
        self.config: dict = {}

    def get(self, path: str):
        def decorate(func):
            self.routes[("GET", path)] = func
            return func

        return decorate

    def post(self, path: str):
        def decorate(func):
            self.routes[("POST", path)] = func
            return func

        return decorate

    def after_request(self, func):
        return func


def load_runtime(tmp_path: Path, monkeypatch):
    player = types.SimpleNamespace(
        status=lambda: {},
        mode_status=lambda: {"mode": "off"},
        play=lambda *_args, **_kwargs: {},
        control=lambda *_args, **_kwargs: {},
        command=lambda *_args, **_kwargs: {},
        switch_mode=lambda mode: {"mode": mode},
    )
    site_registry = types.SimpleNamespace(
        validate_public_url=lambda url: (url, "example.test"),
        lookup=lambda _host: (None, None),
        make_candidate=lambda *_args, **_kwargs: {},
        confirm=lambda candidate: candidate,
    )
    resolver = types.ModuleType("resolver")
    resolver.BrowserChallengeError = type("BrowserChallengeError", (Exception,), {})
    resolver.OriginProtectedError = type("OriginProtectedError", (Exception,), {})
    resolver.resolve_page = lambda _url: {}
    flask = types.ModuleType("flask")
    flask.Flask = FakeFlask
    flask.Response = object
    flask.abort = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"abort:{args}:{kwargs}"))
    flask.jsonify = lambda payload=None, **kwargs: payload if payload is not None else kwargs
    flask.request = types.SimpleNamespace(remote_addr="127.0.0.1", get_json=lambda silent=True: {})
    flask.send_from_directory = lambda *_args, **_kwargs: ""
    monkeypatch.setitem(sys.modules, "player", player)
    monkeypatch.setitem(sys.modules, "site_registry", site_registry)
    monkeypatch.setitem(sys.modules, "resolver", resolver)
    monkeypatch.setitem(sys.modules, "flask", flask)

    spec = importlib.util.spec_from_file_location(f"skeleton_cast_app_{id(tmp_path)}", APP_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.STATE = tmp_path / "state"
    module.JOBS = module.STATE / "jobs"
    module.BASE = tmp_path / "base"
    module.STATIC = tmp_path / "static"
    module.JOBS.mkdir(parents=True)
    module.BASE.mkdir()
    return module


def write_job(module, job_id: str, title: str, sources: list[dict]) -> None:
    module._save(
        {
            "job_id": job_id,
            "status": "ready",
            "title": title,
            "sources": sources,
        }
    )


def source(source_id: str, quality: str, *, season: int = 1, episode: str = "E1") -> dict:
    return {
        "source_id": source_id,
        "url": "https://private.example.invalid/watch",
        "headers": {"Referer": "https://private.example.invalid", "User-Agent": "ua"},
        "quality": quality,
        "translation": "Synthetic",
        "season": season,
        "episode": episode,
        "height": 1080,
    }


def test_stale_saved_selection_reconciles_to_active_player_work(tmp_path, monkeypatch) -> None:
    module = load_runtime(tmp_path, monkeypatch)
    saved_job = "aaaaaaaaaaaaaaaa"
    active_job = "bbbbbbbbbbbbbbbb"
    write_job(module, saved_job, "Synthetic A", [source("source-a", "720p", episode="A1")])
    write_job(module, active_job, "Synthetic B", [source("source-b", "1080p", episode="B1")])
    module.player.status = lambda: {"job_id": active_job, "source_id": "source-b"}

    result = module._reconcile_video_selection({"job_id": saved_job, "source_id": "source-a"})

    assert result["canonical_selection"] == {"job_id": active_job, "source_id": "source-b"}
    assert result["selection_source"] == "active_player"
    assert result["saved_selection_matches_active_player"] is False
    assert result["job"]["job_id"] == active_job
    assert result["selector"]["history_text"] == "Synthetic B"
    assert result["selector"]["source"]["episode"] == "B1"
    assert "url" not in result["job"]["sources"][0]


def test_same_job_stale_source_reconciles_source_and_dependents(tmp_path, monkeypatch) -> None:
    module = load_runtime(tmp_path, monkeypatch)
    job_id = "cccccccccccccccc"
    write_job(module, job_id, "Synthetic Work", [source("source-a", "720p", episode="E1"), source("source-b", "1080p", episode="E2")])
    module.player.status = lambda: {"job_id": job_id, "source_id": "source-b"}

    result = module._reconcile_video_selection({"job_id": job_id, "source_id": "source-a"})

    assert result["canonical_selection"] == {"job_id": job_id, "source_id": "source-b"}
    assert result["selector"]["source"]["source_id"] == "source-b"
    assert result["selector"]["source"]["quality"] == "1080p"
    assert result["selector"]["source"]["episode"] == "E2"


def test_matching_saved_selection_preserves_behavior_without_churn(tmp_path, monkeypatch) -> None:
    module = load_runtime(tmp_path, monkeypatch)
    job_id = "dddddddddddddddd"
    write_job(module, job_id, "Synthetic Match", [source("source-match", "Auto")])
    module.player.status = lambda: {"selection": {"job_id": job_id, "source_id": "source-match"}}

    result = module._reconcile_video_selection({"job_id": job_id, "source_id": "source-match"})

    assert result["selection_source"] == "saved_selection"
    assert result["selection_changed"] is False
    assert result["saved_selection_matches_active_player"] is True
    assert result["selector"]["history_text"] == "Synthetic Match"


def test_no_active_player_preserves_saved_selection_restore(tmp_path, monkeypatch) -> None:
    module = load_runtime(tmp_path, monkeypatch)
    job_id = "eeeeeeeeeeeeeeee"
    write_job(module, job_id, "Synthetic Saved", [source("source-saved", "Auto")])
    module.player.status = lambda: {"state": "idle"}

    result = module._reconcile_video_selection({"job_id": job_id, "source_id": "source-saved"})

    assert result["active_player_valid"] is False
    assert result["canonical_selection"] == {"job_id": job_id, "source_id": "source-saved"}
    assert result["selector"]["history_text"] == "Synthetic Saved"


def test_invalid_active_job_fails_closed_without_corrupting_saved_selection(tmp_path, monkeypatch) -> None:
    module = load_runtime(tmp_path, monkeypatch)
    saved_job = "ffffffffffffffff"
    write_job(module, saved_job, "Synthetic Saved", [source("source-saved", "Auto")])
    module.player.status = lambda: {"job_id": "1111111111111111", "source_id": "missing-source"}

    result = module._reconcile_video_selection({"job_id": saved_job, "source_id": "source-saved"})

    assert result["active_player_valid"] is False
    assert result["active_player_invalid"] is True
    assert result["canonical_selection"] == {"job_id": saved_job, "source_id": "source-saved"}
    assert result["selector"]["history_text"] == "Synthetic Saved"


def test_video_boot_route_returns_reconciled_state(tmp_path, monkeypatch) -> None:
    module = load_runtime(tmp_path, monkeypatch)
    job_id = "1234567890abcdef"
    write_job(module, job_id, "Synthetic Route", [source("source-route", "Auto")])
    module.player.status = lambda: {"job_id": job_id, "source_id": "source-route"}
    monkeypatch.setattr(module, "_trusted", lambda: True)
    module.request = types.SimpleNamespace(
        get_json=lambda silent=True: {"saved_selection": {"job_id": "aaaaaaaaaaaaaaaa", "source_id": "source-old"}}
    )

    response = module.video_boot()

    assert response["canonical_selection"] == {"job_id": job_id, "source_id": "source-route"}
    assert ("POST", "/api/video/boot") in module.app.routes


def test_shell_renders_three_visible_bottom_nav_items_and_remote_route_remains_reachable(tmp_path, monkeypatch) -> None:
    module = load_runtime(tmp_path, monkeypatch)
    (module.BASE / "remote.html").write_text(
        """
        <html><head></head><body>
        <nav class="bottom-nav" aria-label="Навігація">
        <a class="nav-item" href="/phone"><span>Головна</span></a>
        <a class="nav-item" href="/video"><span>Відео</span></a>
        <a class="nav-item active" href="/remote"><span>Пульт</span></a>
        <a class="nav-item" href="/devices"><span>Пристрої</span></a>
        </nav>
        </body></html>
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_trusted", lambda: True)

    html = module.remote()

    assert ("GET", "/remote") in module.app.routes
    assert re.findall(r'<a class="nav-item(?: active)?" href="[^"]+">.*?<span class="nav-label">([^<]+)</span>', html) == [
        "Головна",
        "Відео",
        "Пристрої",
    ]
    assert 'href="/remote"' not in html
    assert "Пульт" not in re.sub(r"<main.*?</main>", "", html, flags=re.S)
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in html
    assert ".mode-button .mode-label,.bottom-nav .nav-label{font-size:var(--mode-button-label-size)}" in html
    assert "@media (max-width:360px){.mode-button .mode-label,.bottom-nav .nav-label{font-size:var(--mode-button-label-size-narrow)}}" in html
    assert "--home-shell-version:\"v124\"" in html


def test_checked_in_remote_shell_hides_remote_nav_but_keeps_control_capability() -> None:
    source_text = (ROOT / "core/home_edge/static/adaptive_remote.html").read_text(encoding="utf-8")
    app_text = APP_PATH.read_text(encoding="utf-8")

    nav = re.search(r'<nav class="bottom-nav"[^>]*>(.*?)</nav>', source_text, re.S)
    assert nav
    assert re.findall(r'<span class="nav-label">([^<]+)</span>', nav.group(1)) == ["Головна", "Відео", "Пристрої"]
    assert 'href="/remote"' not in nav.group(1)
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in source_text
    assert ".mode-button .mode-label,.bottom-nav .nav-label{font-size:var(--mode-button-label-size)}" in source_text
    assert "@app.get('/remote')" in app_text
    assert "@app.post('/api/remote/control')" in app_text
