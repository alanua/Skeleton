from __future__ import annotations

import base64
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

from core import private_static_site_runtime as runtime


CERT = """-----BEGIN CERTIFICATE-----
MIIBtestPUBLIConly==
-----END CERTIFICATE-----
"""


def _prepare_body(artifact_id: str = "trip-site") -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {runtime.PREPARE_TASK_ID}",
            f"Operator Approval: {runtime.PREPARE_APPROVAL}",
            f"Artifact ID: {artifact_id}",
        )
    )


def _deploy_body(payload: bytes, artifact_id: str = "trip-site", url_path: str = "/travel/trip-site") -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    digest = hashlib.sha256(payload).hexdigest()
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {runtime.DEPLOY_TASK_ID}",
            f"Operator Approval: {runtime.DEPLOY_APPROVAL}",
            f"Artifact ID: {artifact_id}",
            f"URL Path: {url_path}",
            f"Encrypted Payload SHA-256: {digest}",
            f"Plaintext ZIP SHA-256: {digest}",
            "```encrypted-payload-base64",
            encoded,
            "```",
        )
    )


def _zip_bytes(entries: dict[str, bytes], modes: dict[str, int] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name)
            mode = (modes or {}).get(name, 0o100644)
            info.external_attr = mode << 16
            archive.writestr(info, content)
    return buffer.getvalue()


class FakeRunner:
    def __init__(self, *, cms_fails: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.cms_fails = cms_fails

    def __call__(self, args: list[str], cwd: Path | None, timeout: int) -> tuple[int, str]:
        self.commands.append(args)
        if args[:3] == ["openssl", "req", "-x509"]:
            Path(args[args.index("-keyout") + 1]).write_text("PRIVATE", encoding="ascii")
            Path(args[args.index("-out") + 1]).write_text(CERT, encoding="ascii")
            return 0, ""
        if args[:3] == ["openssl", "x509", "-in"]:
            return 0, "SHA256 Fingerprint=AA:BB\nnotAfter=Jul 19 00:00:00 2026 GMT\n"
        if args[:3] == ["openssl", "cms", "-encrypt"]:
            Path(args[args.index("-out") + 1]).write_bytes(Path(args[-1]).read_bytes())
            return 0, ""
        if args[:3] == ["openssl", "cms", "-decrypt"]:
            if self.cms_fails:
                return 1, "private failure details"
            Path(args[args.index("-out") + 1]).write_bytes(Path(args[args.index("-in") + 1]).read_bytes())
            return 0, ""
        if args == ["tailscale", "serve", "status", "--json"]:
            return 0, json.dumps({"TCP": {"443": {"HTTPS": True}}, "Web": {"/other": "kept"}})
        if args == ["tailscale", "status", "--json"]:
            return 0, json.dumps({"Self": {"DNSName": "node.tailnet.ts.net."}})
        if args[:2] == ["tailscale", "serve"]:
            return 0, ""
        return 1, "unexpected"


def _fetcher(url: str, timeout: int) -> tuple[int, bytes]:
    if url.endswith("/style.css"):
        return 200, b"body{}"
    return 200, b"<html><head><title>Trip</title><link href=\"style.css\"></head><body></body></html>"


def _prepared_handoff(root: Path, runner: FakeRunner) -> None:
    report = runtime.prepare_private_static_site_handoff(
        _prepare_body(), root=root, command_runner=runner
    )
    assert report.startswith("DONE:")


def test_prepare_requires_exact_approval_and_valid_artifact(tmp_path: Path) -> None:
    bad_approval = _prepare_body().replace(runtime.PREPARE_APPROVAL, "approve")
    assert "reason=missing_operator_approval" in runtime.prepare_private_static_site_handoff(bad_approval, root=tmp_path)
    assert "reason=invalid_artifact_id" in runtime.prepare_private_static_site_handoff(_prepare_body("../bad"), root=tmp_path)


def test_private_root_env_override_is_confined(monkeypatch) -> None:
    monkeypatch.setenv(runtime.ROOT_ENV, "/tmp/not-skeleton/private")
    report = runtime.prepare_private_static_site_handoff(_prepare_body(), command_runner=FakeRunner())
    assert "reason=private_root_outside_allowed_base" in report


def test_prepare_creates_non_overwritten_public_certificate_handoff(tmp_path: Path) -> None:
    runner = FakeRunner()
    report = runtime.prepare_private_static_site_handoff(_prepare_body(), root=tmp_path, command_runner=runner)
    assert report.startswith("DONE:")
    assert "artifact_id=trip-site" in report
    assert "durable_handoff_status=DURABLE_HANDOFF_READY" in report
    assert "public_certificate_pem_start" in report
    assert "BEGIN CERTIFICATE" in report
    assert "PRIVATE KEY" not in report
    assert stat.S_IMODE((tmp_path / "handoffs" / "trip-site").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "handoffs" / "trip-site" / "private_key.pem").stat().st_mode) == 0o600
    second = runtime.prepare_private_static_site_handoff(_prepare_body(), root=tmp_path, command_runner=runner)
    assert "reason=handoff_already_exists" in second


def test_deploy_rejects_invalid_metadata_and_hash(tmp_path: Path) -> None:
    _prepared_handoff(tmp_path, FakeRunner())
    payload = _zip_bytes({"index.html": b"<title>Trip</title>"})
    bad_path = _deploy_body(payload, url_path="/bad/trip-site")
    assert "reason=invalid_url_path" in runtime.deploy_private_static_site(bad_path, root=tmp_path)
    bad_hash = _deploy_body(payload).replace(hashlib.sha256(payload).hexdigest(), "0" * 64, 1)
    assert "reason=ciphertext_sha256_mismatch" in runtime.deploy_private_static_site(bad_hash, root=tmp_path)


def test_deploy_reports_cms_failure_without_private_output(tmp_path: Path) -> None:
    _prepared_handoff(tmp_path, FakeRunner())
    payload = _zip_bytes({"index.html": b"<title>Trip</title>"})
    report = runtime.deploy_private_static_site(
        _deploy_body(payload), root=tmp_path, command_runner=FakeRunner(cms_fails=True)
    )
    assert "reason=cms_decrypt_failed" in report
    assert "private failure details" not in report


def test_deploy_success_publishes_verifies_and_cleans_handoff(tmp_path: Path) -> None:
    runner = FakeRunner()
    _prepared_handoff(tmp_path, runner)
    payload = _zip_bytes(
        {
            "site/index.html": b"<html><head><title>Trip</title><link href=\"style.css\"></head></html>",
            "site/style.css": b"body{}",
        }
    )
    report = runtime.deploy_private_static_site(
        _deploy_body(payload), root=tmp_path, command_runner=runner, url_fetcher=_fetcher
    )
    assert report.startswith("DONE:")
    assert "private_tailscale_url=https://node.tailnet.ts.net/travel/trip-site" in report
    assert "ciphertext_sha256_match=true" in report
    assert "plaintext_zip_sha256_match=true" in report
    assert "asset_count=1" in report
    assert "serve_private=true" in report
    assert "cleanup_status=secrets_removed" in report
    assert (tmp_path / "sites" / "trip-site" / "current" / "index.html").is_file()
    assert not (tmp_path / "handoffs" / "trip-site").exists()
    serve_commands = [command for command in runner.commands if command[:2] == ["tailscale", "serve"]]
    assert any("--set-path=/travel/trip-site" in command for command in serve_commands)
    assert all("funnel" not in command and "reset" not in command for command in serve_commands for command in command)


def test_deploy_rolls_back_only_requested_path_on_verification_failure(tmp_path: Path) -> None:
    runner = FakeRunner()
    _prepared_handoff(tmp_path, runner)
    payload = _zip_bytes({"index.html": b"<title>Trip</title>"})
    report = runtime.deploy_private_static_site(
        _deploy_body(payload),
        root=tmp_path,
        command_runner=runner,
        url_fetcher=lambda url, timeout: (404, b""),
    )
    assert "reason=https_verification_failed" in report
    assert ["tailscale", "serve", "--bg", "--yes", "--https=443", "--set-path=/travel/trip-site", "off"] in runner.commands


def test_deploy_rejects_plaintext_hash_mismatch(tmp_path: Path) -> None:
    _prepared_handoff(tmp_path, FakeRunner())
    payload = _zip_bytes({"index.html": b"<title>Trip</title>"})
    body = _deploy_body(payload).replace(f"Plaintext ZIP SHA-256: {hashlib.sha256(payload).hexdigest()}", "Plaintext ZIP SHA-256: " + "1" * 64)
    report = runtime.deploy_private_static_site(body, root=tmp_path, command_runner=FakeRunner())
    assert "reason=plaintext_zip_sha256_mismatch" in report


def test_deploy_rejects_zip_validation_failures(tmp_path: Path) -> None:
    cases = {
        "traversal": _zip_bytes({"../index.html": b"x"}),
        "symlink": _zip_bytes({"index.html": b"x"}, {"index.html": stat.S_IFLNK | 0o777}),
        "executable": _zip_bytes({"index.html": b"x"}, {"index.html": 0o100755}),
        "size": _zip_bytes({"index.html": b"x" * (runtime.MAX_UNCOMPRESSED_BYTES + 1)}),
        "entry_count": _zip_bytes({f"f{i}.txt": b"x" for i in range(runtime.MAX_ZIP_ENTRIES + 1)}),
        "extension": _zip_bytes({"index.exe": b"x"}),
        "nested_archive": _zip_bytes({"site.zip": b"x"}),
        "missing_index": _zip_bytes({"style.css": b"x"}),
        "private_marker": _zip_bytes({"index.html": b"<title>x</title>-----BEGIN PRIVATE KEY-----"}),
    }
    for payload in cases.values():
        root = tmp_path / hashlib.sha256(payload).hexdigest()[:8]
        runner = FakeRunner()
        _prepared_handoff(root, runner)
        report = runtime.deploy_private_static_site(_deploy_body(payload), root=root, command_runner=runner)
        assert report.startswith("BLOCKED:")
