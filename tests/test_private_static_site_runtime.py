from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from core import private_static_site_runtime as runtime


CERT = """-----BEGIN CERTIFICATE-----
MIIBtestPUBLIConly==
-----END CERTIFICATE-----
"""
RESULT_DER = b"deterministic-result-certificate-der"
RESULT_DER_SHA256 = hashlib.sha256(RESULT_DER).hexdigest()


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
            f"Result Certificate DER SHA-256: {RESULT_DER_SHA256}",
            "```result-certificate-pem",
            CERT.strip(),
            "```",
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
    def __init__(
        self,
        *,
        cms_fails: bool = False,
        existing_paths: tuple[str, ...] = (),
        serve_set_fails: bool = False,
        sudo_succeeds: bool = True,
    ) -> None:
        self.commands: list[list[str]] = []
        self.cms_fails = cms_fails
        self.existing_paths = existing_paths
        self.serve_set_fails = serve_set_fails
        self.sudo_succeeds = sudo_succeeds

    def __call__(self, args: list[str], cwd: Path | None, timeout: int) -> tuple[int, str]:
        self.commands.append(args)
        if args[:3] == ["openssl", "req", "-x509"]:
            Path(args[args.index("-keyout") + 1]).write_text("PRIVATE", encoding="ascii")
            Path(args[args.index("-out") + 1]).write_text(CERT, encoding="ascii")
            return 0, ""
        if args[:3] == ["openssl", "x509", "-in"]:
            if "-outform" in args:
                Path(args[args.index("-out") + 1]).write_bytes(RESULT_DER)
                return 0, ""
            return 0, "SHA256 Fingerprint=AA:BB\nnotAfter=Jul 19 00:00:00 2026 GMT\n"
        if args[:3] == ["openssl", "cms", "-encrypt"]:
            if "-in" not in args:
                return 1, "missing -in"
            Path(args[args.index("-out") + 1]).write_bytes(Path(args[args.index("-in") + 1]).read_bytes())
            return 0, ""
        if args[:3] == ["openssl", "cms", "-decrypt"]:
            if self.cms_fails:
                return 1, "private failure details"
            Path(args[args.index("-out") + 1]).write_bytes(Path(args[args.index("-in") + 1]).read_bytes())
            return 0, ""
        if args == ["tailscale", "serve", "status", "--json"]:
            web = {"/other": {"Handlers": {"Path": "/srv/other"}}}
            for path in self.existing_paths:
                web[path] = {"Handlers": {"Path": f"/srv{path}"}}
            return 0, json.dumps({"TCP": {"443": {"HTTPS": True}}, "Web": web}, sort_keys=True)
        if args == ["tailscale", "status", "--json"]:
            return 0, json.dumps({"Self": {"DNSName": "unit-host.example.invalid."}})
        if args[:4] == ["sudo", "-n", "tailscale", "serve"]:
            return (0, "") if self.sudo_succeeds else (1, "sudo failed")
        if args[:2] == ["tailscale", "serve"]:
            if self.serve_set_fails:
                return 1, "permission denied"
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
    assert "private_tailscale_url" not in report
    assert "unit-host.example.invalid" not in report
    assert "encrypted_result_cms_b64_start" in report
    assert re.search(r"encrypted_result_cms_sha256=[0-9a-f]{64}", report)
    assert re.search(r"encrypted_result_cms_bytes=[1-9][0-9]*", report)
    assert "ciphertext_sha256_match=true" in report
    assert "plaintext_zip_sha256_match=true" in report
    assert "result_certificate_der_sha256_match=true" in report
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
    assert not (tmp_path / "sites" / "trip-site" / "current").exists()


def test_deploy_restores_prior_current_on_verification_failure(tmp_path: Path) -> None:
    runner = FakeRunner()
    _prepared_handoff(tmp_path, runner)
    current = tmp_path / "sites" / "trip-site" / "current"
    current.mkdir(parents=True)
    (current / "index.html").write_text("<title>Old</title>", encoding="utf-8")
    payload = _zip_bytes({"index.html": b"<title>New</title>"})

    report = runtime.deploy_private_static_site(
        _deploy_body(payload),
        root=tmp_path,
        command_runner=runner,
        url_fetcher=lambda url, timeout: (404, b""),
    )

    assert "reason=https_verification_failed" in report
    assert (current / "index.html").read_text(encoding="utf-8") == "<title>Old</title>"


def test_deploy_refuses_existing_tailscale_path_without_mutation(tmp_path: Path) -> None:
    runner = FakeRunner(existing_paths=("/travel/trip-site",))
    _prepared_handoff(tmp_path, runner)
    payload = _zip_bytes({"index.html": b"<title>Trip</title>"})

    report = runtime.deploy_private_static_site(
        _deploy_body(payload),
        root=tmp_path,
        command_runner=runner,
        url_fetcher=_fetcher,
    )

    assert "reason=tailscale_path_already_configured" in report
    serve_commands = [command for command in runner.commands if command[:2] == ["tailscale", "serve"]]
    assert serve_commands == [["tailscale", "serve", "status", "--json"]]


def test_deploy_uses_sudo_fallback_for_set_and_remove_path(tmp_path: Path) -> None:
    runner = FakeRunner(serve_set_fails=True)
    _prepared_handoff(tmp_path, runner)
    payload = _zip_bytes({"index.html": b"<title>Trip</title>"})

    report = runtime.deploy_private_static_site(
        _deploy_body(payload),
        root=tmp_path,
        command_runner=runner,
        url_fetcher=lambda url, timeout: (404, b""),
    )

    assert "reason=https_verification_failed" in report
    assert ["sudo", "-n", "tailscale", "serve", "--bg", "--yes", "--https=443", "--set-path=/travel/trip-site", str((tmp_path / "sites" / "trip-site" / "current").resolve(strict=False))] in runner.commands
    assert ["sudo", "-n", "tailscale", "serve", "--bg", "--yes", "--https=443", "--set-path=/travel/trip-site", "off"] in runner.commands


def test_deploy_rejects_plaintext_hash_mismatch(tmp_path: Path) -> None:
    _prepared_handoff(tmp_path, FakeRunner())
    payload = _zip_bytes({"index.html": b"<title>Trip</title>"})
    body = _deploy_body(payload).replace(f"Plaintext ZIP SHA-256: {hashlib.sha256(payload).hexdigest()}", "Plaintext ZIP SHA-256: " + "1" * 64)
    report = runtime.deploy_private_static_site(body, root=tmp_path, command_runner=FakeRunner())
    assert "reason=plaintext_zip_sha256_mismatch" in report


def test_deploy_requires_and_validates_result_certificate(tmp_path: Path) -> None:
    _prepared_handoff(tmp_path, FakeRunner())
    payload = _zip_bytes({"index.html": b"<title>Trip</title>"})
    missing = _deploy_body(payload).replace(
        f"Result Certificate DER SHA-256: {RESULT_DER_SHA256}\n"
        "```result-certificate-pem\n"
        f"{CERT.strip()}\n"
        "```\n",
        "",
    )
    assert "reason=invalid_result_certificate_der_sha256" in runtime.deploy_private_static_site(missing, root=tmp_path)
    bad_hash = _deploy_body(payload).replace(RESULT_DER_SHA256, "1" * 64)
    report = runtime.deploy_private_static_site(bad_hash, root=tmp_path, command_runner=FakeRunner())
    assert "reason=result_certificate_der_sha256_mismatch" in report


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


def test_deploy_accepts_milan_shaped_zip_with_directory_modes(tmp_path: Path) -> None:
    runner = FakeRunner()
    _prepared_handoff(tmp_path, runner)
    payload = _zip_bytes(
        {
            "milan/": b"",
            "milan/assets/": b"",
            "milan/index.html": b"<html><head><title>Milan</title><link href=\"assets/site.css\"></head></html>",
            "milan/assets/site.css": b"body{}",
        },
        {
            "milan/": stat.S_IFDIR | 0o755,
            "milan/assets/": stat.S_IFDIR | 0o755,
            "milan/index.html": stat.S_IFREG | 0o644,
            "milan/assets/site.css": stat.S_IFREG | 0o644,
        },
    )

    report = runtime.deploy_private_static_site(
        _deploy_body(payload), root=tmp_path, command_runner=runner, url_fetcher=_fetcher
    )

    assert report.startswith("DONE:")
    assert (tmp_path / "sites" / "trip-site" / "current" / "assets" / "site.css").is_file()


def test_real_openssl_prepare_payload_and_result_roundtrip(tmp_path: Path) -> None:
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not available")

    def openssl_runner(args: list[str], cwd: Path | None, timeout: int) -> tuple[int, str]:
        if args[0] == "openssl":
            result = subprocess.run(
                args,
                cwd=str(cwd) if cwd is not None else None,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout + result.stderr
        return FakeRunner()(args, cwd, timeout)

    result_key = tmp_path / "result-key.pem"
    result_cert = tmp_path / "result-cert.pem"
    result_der = tmp_path / "result-cert.der"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(result_key),
            "-out",
            str(result_cert),
            "-days",
            "1",
            "-nodes",
            "-sha256",
            "-subj",
            "/CN=result-recipient",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=runtime.OPENSSL_TIMEOUT,
    )
    subprocess.run(
        [openssl, "x509", "-in", str(result_cert), "-outform", "DER", "-out", str(result_der)],
        check=True,
        capture_output=True,
        text=True,
        timeout=runtime.OPENSSL_TIMEOUT,
    )
    result_cert_hash = hashlib.sha256(result_der.read_bytes()).hexdigest()

    prepare_report = runtime.prepare_private_static_site_handoff(
        _prepare_body(), root=tmp_path, command_runner=openssl_runner
    )
    assert prepare_report.startswith("DONE:")

    zip_payload = _zip_bytes({"index.html": b"<html><head><title>Trip</title></head></html>"})
    zip_path = tmp_path / "payload.zip"
    cms_path = tmp_path / "payload.cms"
    zip_path.write_bytes(zip_payload)
    subprocess.run(
        [
            openssl,
            "cms",
            "-encrypt",
            "-binary",
            "-aes256",
            "-outform",
            "DER",
            "-in",
            str(zip_path),
            "-out",
            str(cms_path),
            str(tmp_path / "handoffs" / "trip-site" / "certificate.pem"),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=runtime.OPENSSL_TIMEOUT,
    )
    ciphertext = cms_path.read_bytes()
    body = "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {runtime.DEPLOY_TASK_ID}",
            f"Operator Approval: {runtime.DEPLOY_APPROVAL}",
            "Artifact ID: trip-site",
            "URL Path: /travel/trip-site",
            f"Encrypted Payload SHA-256: {hashlib.sha256(ciphertext).hexdigest()}",
            f"Plaintext ZIP SHA-256: {hashlib.sha256(zip_payload).hexdigest()}",
            f"Result Certificate DER SHA-256: {result_cert_hash}",
            "```result-certificate-pem",
            result_cert.read_text(encoding="ascii").strip(),
            "```",
            "```encrypted-payload-base64",
            base64.b64encode(ciphertext).decode("ascii"),
            "```",
        )
    )
    fake_tailscale = FakeRunner()

    def hybrid_runner(args: list[str], cwd: Path | None, timeout: int) -> tuple[int, str]:
        if args[0] == "openssl":
            result = subprocess.run(
                args,
                cwd=str(cwd) if cwd is not None else None,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout + result.stderr
        return fake_tailscale(args, cwd, timeout)

    report = runtime.deploy_private_static_site(
        body, root=tmp_path, command_runner=hybrid_runner, url_fetcher=_fetcher
    )
    assert report.startswith("DONE:")
    assert "unit-host.example.invalid" not in report
    encrypted_match = re.search(
        r"(?ms)^encrypted_result_cms_b64_start\n(?P<payload>[A-Za-z0-9+/=\r\n]+)\nencrypted_result_cms_b64_end$",
        report,
    )
    assert encrypted_match is not None
    result_cms = tmp_path / "result-output.cms"
    result_json = tmp_path / "result-output.json"
    result_cms.write_bytes(base64.b64decode("".join(encrypted_match.group("payload").split()), validate=True))
    subprocess.run(
        [
            openssl,
            "cms",
            "-decrypt",
            "-binary",
            "-inform",
            "DER",
            "-in",
            str(result_cms),
            "-recip",
            str(result_cert),
            "-inkey",
            str(result_key),
            "-out",
            str(result_json),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=runtime.OPENSSL_TIMEOUT,
    )
    payload = json.loads(result_json.read_text(encoding="utf-8"))
    assert payload["private_url"] == "https://" + "unit-host.example.invalid" + "/travel/trip-site"
    assert payload["cleanup_status"] == "secrets_removed"
