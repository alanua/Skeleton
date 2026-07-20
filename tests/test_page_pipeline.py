from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
from pathlib import Path

from core.page_pipeline import build_manifest, publish_manifest, register_downstream_action


def _site(tmp_path: Path, text: str = "Hello", *, with_assets: bool = True) -> tuple[Path, Path]:
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    if with_assets:
        (site / "styles.css").write_text("body{font-family:system-ui;background:#fff}", encoding="utf-8")
        (site / "app.js").write_text("document.body.dataset.ready='1'", encoding="utf-8")
        html = f'<!doctype html><html><head><title>Demo</title><link rel="stylesheet" href="styles.css"></head><body><h1>{text}</h1><script src="app.js"></script></body></html>'
    else:
        html = f"<!doctype html><html><head><title>Demo</title></head><body><h1>{text}</h1></body></html>"
    (site / "index.html").write_text(html, encoding="utf-8")
    assets = tmp_path / "assets.json"
    assets.write_text('{"assets":[]}', encoding="utf-8")
    return site, assets


def _manifest(tmp_path: Path, profile: str, mode: str = "create", text: str = "Hello", **options):
    site, assets = _site(tmp_path, text)
    manifest = {
        "schema_version": 1,
        "owner_module": "demo",
        "publication_profile_id": profile,
        "page_id": "demo-page",
        "template_id": "static_directory_v1",
        "content_ref": str(site),
        "asset_manifest_ref": str(assets),
        "publication_mode": mode,
        "operator_approval": "publish_page_v1",
        "backend_options": options,
    }
    return manifest, site


def _decode_b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def _hkdf(secret: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    output = b""
    block = b""
    counter = 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:length]


def _decrypt(target: Path, key_text: str, tmp_path: Path) -> bytes:
    payload = json.loads((target / "payload.json").read_text(encoding="utf-8"))
    secret = _decode_b64(key_text)
    salt = _decode_b64(payload["salt"])
    iv = _decode_b64(payload["iv"])
    cipher = _decode_b64(payload["ciphertext"])
    tag = _decode_b64(payload["mac"])
    material = _hkdf(secret, salt, b"skeleton-page-publisher-v1", 64)
    assert hmac.compare_digest(tag, hmac.new(material[32:], b"SPP1" + salt + iv + cipher, hashlib.sha256).digest())
    cipher_path = tmp_path / "cipher.bin"
    plain_path = tmp_path / "plain.html"
    cipher_path.write_bytes(cipher)
    subprocess.run([
        "openssl", "enc", "-d", "-aes-256-ctr", "-K", material[:32].hex(), "-iv", iv.hex(),
        "-in", str(cipher_path), "-out", str(plain_path),
    ], check=True)
    return plain_path.read_bytes()


def test_build_is_deterministic(tmp_path: Path):
    manifest, _ = _manifest(tmp_path, "filesystem_static_v1", root=str(tmp_path / "published"))
    first, artifact1 = build_manifest(manifest, root=tmp_path / "runtime")
    second, artifact2 = build_manifest(manifest, root=tmp_path / "runtime")
    try:
        assert first["content_hash"] == second["content_hash"]
        assert first["package_hash"] == second["package_hash"]
        assert artifact1.package_path.read_bytes() == artifact2.package_path.read_bytes()
    finally:
        import shutil
        shutil.rmtree(artifact1.rendered_dir, ignore_errors=True)
        shutil.rmtree(artifact2.rendered_dir, ignore_errors=True)


def test_filesystem_create_update_and_no_change(tmp_path: Path):
    root = tmp_path / "runtime"
    published = tmp_path / "published"
    manifest, site = _manifest(tmp_path, "filesystem_static_v1", root=str(published))
    first = publish_manifest(manifest, root=root)
    assert first["status"] == "PUBLISHED"
    target = published / "pages" / "demo-page"
    assert (target / "index.html").is_file()

    manifest["publication_mode"] = "update_owned"
    unchanged = publish_manifest(manifest, root=root)
    assert unchanged["status"] == "NO_CHANGE"

    (site / "index.html").write_text("<!doctype html><html><head><title>Demo</title></head><body>Changed</body></html>", encoding="utf-8")
    changed = publish_manifest(manifest, root=root)
    assert changed["status"] == "PUBLISHED"
    assert "Changed" in (target / "index.html").read_text(encoding="utf-8")
    assert changed["revision"] != first["revision"]


def test_route_ownership_is_enforced(tmp_path: Path):
    published = tmp_path / "published"
    target = published / "pages" / "demo-page"
    target.mkdir(parents=True)
    (target / "index.html").write_text("<!doctype html><title>Other</title>", encoding="utf-8")
    (target / ".skeleton-page-owner.json").write_text(json.dumps({
        "owner_module": "other", "page_id": "demo-page", "profile_id": "filesystem_static_v1"
    }), encoding="utf-8")
    manifest, _ = _manifest(tmp_path, "filesystem_static_v1", mode="update_owned", root=str(published))
    result = publish_manifest(manifest, root=tmp_path / "runtime")
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "publication_path_not_owned"


def test_github_pages_encryption_and_stable_key(tmp_path: Path):
    repo = tmp_path / "pages-repo"
    repo.mkdir()
    manifest, site = _manifest(
        tmp_path,
        "github_pages_encrypted_v1",
        repository_path=str(repo),
        base_url="https://example.test/Travel",
        git_mode="none",
        verification_mode="local",
    )
    root = tmp_path / "runtime"
    first = publish_manifest(manifest, root=root)
    assert first["status"] == "PUBLISHED"
    target = repo / "demo-page"
    combined = (target / "index.html").read_text(encoding="utf-8") + (target / "payload.json").read_text(encoding="utf-8")
    assert "Hello" not in combined
    state_path = root / "state" / "github_pages_encrypted_v1" / "demo" / "demo-page.json"
    state1 = json.loads(state_path.read_text(encoding="utf-8"))
    key1 = state1["private"]["fragment_key"]
    plain = _decrypt(target, key1, tmp_path)
    assert b"Hello" in plain
    assert b"styles.css" not in plain
    assert b"app.js" not in plain

    manifest["publication_mode"] = "update_owned"
    (site / "index.html").write_text('<!doctype html><html><head><title>Demo</title><link rel="stylesheet" href="styles.css"></head><body><h1>Updated</h1><script src="app.js"></script></body></html>', encoding="utf-8")
    second = publish_manifest(manifest, root=root)
    assert second["status"] == "PUBLISHED"
    state2 = json.loads(state_path.read_text(encoding="utf-8"))
    assert state2["private"]["fragment_key"] == key1
    assert b"Updated" in _decrypt(target, key1, tmp_path)


def test_https_failure_rolls_back_and_gates_actions(tmp_path: Path):
    repo = tmp_path / "pages-repo"
    repo.mkdir()
    calls: list[str] = []

    def action(receipt, config):
        calls.append(receipt["revision"])
        return {"ok": True}

    register_downstream_action("test_action", action, replace=True)
    manifest, _ = _manifest(
        tmp_path,
        "github_pages_encrypted_v1",
        repository_path=str(repo),
        base_url="https://example.test/Travel",
        git_mode="none",
        verification_mode="https",
        verify_attempts=1,
        verify_delay_seconds=0,
    )
    manifest["downstream_actions"] = ["test_action"]

    def fetcher(url: str, timeout: int):
        return 404, b""

    result = publish_manifest(manifest, root=tmp_path / "runtime", url_fetcher=fetcher)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "https_verification_failed"
    assert calls == []
    assert not (repo / "demo-page").exists()


def test_external_image_requires_metadata(tmp_path: Path):
    site, assets = _site(tmp_path, with_assets=False)
    (site / "index.html").write_text('<!doctype html><html><head><title>Demo</title></head><body><img src="https://images.example/x.jpg" alt="City"></body></html>', encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "owner_module": "demo",
        "publication_profile_id": "filesystem_static_v1",
        "page_id": "demo-page",
        "template_id": "static_directory_v1",
        "content_ref": str(site),
        "asset_manifest_ref": str(assets),
        "publication_mode": "create",
        "operator_approval": "publish_page_v1",
        "backend_options": {"root": str(tmp_path / "published")},
    }
    result = publish_manifest(manifest, root=tmp_path / "runtime")
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "external_image_metadata_missing"

    assets.write_text(json.dumps({"assets": [{
        "subject": "City", "asset_url": "https://images.example/x.jpg", "source_url": "https://images.example/source",
        "author": "Author", "license": "CC BY 4.0", "retrieval_date": "2026-07-20", "alt_text": "City"
    }]}), encoding="utf-8")
    result = publish_manifest(manifest, root=tmp_path / "runtime2")
    assert result["status"] == "PUBLISHED"


def test_downstream_action_receives_private_url_after_verification(tmp_path: Path):
    repo = tmp_path / "pages-repo"
    repo.mkdir()
    seen: list[str] = []

    def action(receipt, config):
        seen.append(receipt["private_url"])
        return {"updated": 1}

    register_downstream_action("capture_private_url", action, replace=True)
    manifest, _ = _manifest(
        tmp_path,
        "github_pages_encrypted_v1",
        repository_path=str(repo),
        base_url="https://example.test/Travel",
        git_mode="none",
        verification_mode="local",
    )
    manifest["downstream_actions"] = ["capture_private_url"]
    result = publish_manifest(manifest, root=tmp_path / "runtime")
    assert result["status"] == "PUBLISHED"
    assert seen and seen[0].startswith("https://example.test/Travel/demo-page/#k=")
    assert "private_url" not in result
    assert result["downstream_actions"][0]["status"] == "DONE"


def test_tailscale_profile_uses_existing_safe_runtime(tmp_path: Path, monkeypatch):
    import shutil
    import types
    import core

    def publish_site(source, current):
        current.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, current)
        return current, None

    fake = types.SimpleNamespace(
        _tailscale_serve_status=lambda runner: ({"Web": {}}, None),
        _tailscale_path_configured=lambda status, path: (False, True),
        _publish_site=publish_site,
        _set_tailscale_path=lambda runner, path, directory: (0, ""),
        _tailscale_magic_dns_name=lambda runner: "host.example.invalid",
        _verify_deployed_site=lambda url, site, fetcher: (True, 0),
        _remove_tailscale_path=lambda runner, path: (0, ""),
        _restore_published_site=lambda current, previous: None,
    )
    monkeypatch.setitem(__import__("sys").modules, "core.page_publisher_runtime", fake)
    monkeypatch.setattr(core, "page_publisher_runtime", fake, raising=False)
    site, assets = _site(tmp_path)
    manifest = {
        "schema_version": 1,
        "owner_module": "travel",
        "publication_profile_id": "travel_private_v1",
        "page_id": "trip-page",
        "template_id": "static_directory_v1",
        "content_ref": str(site),
        "asset_manifest_ref": str(assets),
        "publication_mode": "create",
        "operator_approval": "publish_page_v1",
    }
    result = publish_manifest(manifest, root=tmp_path / "runtime", command_runner=lambda *args: (0, ""), url_fetcher=lambda *args: (200, b""))
    assert result["status"] == "PUBLISHED"
    assert result["stable_url"] == "https://host.example.invalid/travel/trip-page"
