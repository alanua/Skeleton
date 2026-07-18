from __future__ import annotations

import base64
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

from core import page_publisher_runtime as runtime
from core.page_publisher_profiles import get_profile, validate_registry

CERT = """-----BEGIN CERTIFICATE-----
MIIBtestPUBLIConly==
-----END CERTIFICATE-----
"""
RESULT_DER = b"result-der"


class FakeRunner:
    def __init__(self, *, status_ok: bool = True, existing_path: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.status_ok = status_ok
        self.existing_path = existing_path

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
            Path(args[args.index("-out") + 1]).write_bytes(Path(args[args.index("-in") + 1]).read_bytes())
            return 0, ""
        if args[:3] == ["openssl", "cms", "-decrypt"]:
            Path(args[args.index("-out") + 1]).write_bytes(Path(args[args.index("-in") + 1]).read_bytes())
            return 0, ""
        if args == ["tailscale", "serve", "status", "--json"]:
            if not self.status_ok:
                return 1, "failed"
            web = {"/other": {"Handlers": {"Path": "/srv/other"}}}
            if self.existing_path:
                web["/travel/trip-site"] = {"Handlers": {"Path": "/srv/existing"}}
            return 0, json.dumps({"Web": web})
        if args == ["tailscale", "status", "--json"]:
            return 0, json.dumps({"Self": {"DNSName": "host.example.invalid."}})
        if args[:2] == ["tailscale", "serve"] or args[:4] == ["sudo", "-n", "tailscale", "serve"]:
            return 0, ""
        return 1, "unexpected"


def zip_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        directory = zipfile.ZipInfo("site/")
        directory.external_attr = (stat.S_IFDIR | 0o755) << 16
        archive.writestr(directory, b"")
        index = zipfile.ZipInfo("site/index.html")
        index.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(index, b"<html><head><title>Trip</title></head><body>ok</body></html>")
    return output.getvalue()


def prepare_body() -> str:
    return "\n".join([
        "Maintenance Task ID: prepare_page_publication_handoff",
        "Operator Approval: prepare_page_publication_handoff_v1",
        "Publication Profile ID: travel_private_v1",
        "Page ID: trip-site",
    ])


def publish_body(payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    result_hash = hashlib.sha256(RESULT_DER).hexdigest()
    return "\n".join([
        "Maintenance Task ID: publish_static_page",
        "Operator Approval: publish_static_page_v1",
        "Publication Profile ID: travel_private_v1",
        "Page ID: trip-site",
        f"Encrypted Payload SHA-256: {digest}",
        f"Plaintext ZIP SHA-256: {digest}",
        f"Result Certificate DER SHA-256: {result_hash}",
        "```result-certificate-pem",
        CERT.strip(),
        "```",
        "```encrypted-payload-base64",
        base64.b64encode(payload).decode("ascii"),
        "```",
    ])


def fetcher(url: str, timeout: int) -> tuple[int, bytes]:
    return 200, b"<html><head><title>Trip</title></head><body>ok</body></html>"


def test_registry_and_travel_path_are_profile_driven() -> None:
    validate_registry()
    profile = get_profile("travel_private_v1")
    assert profile is not None
    assert profile.url_path("milan-2026") == "/travel/milan-2026"


def test_prepare_and_publish_success(tmp_path: Path) -> None:
    runner = FakeRunner()
    prepare = runtime.prepare_page_publication_handoff(prepare_body(), root=tmp_path, command_runner=runner)
    assert prepare.startswith("DONE:")
    payload = zip_bytes()
    report = runtime.publish_static_page(publish_body(payload), root=tmp_path, command_runner=runner, url_fetcher=fetcher)
    assert report.startswith("DONE:")
    assert "publication_profile_id=travel_private_v1" in report
    assert "host.example.invalid" not in report
    assert (tmp_path / "sites" / "travel_private_v1" / "trip-site" / "current" / "index.html").is_file()
    assert not (tmp_path / "handoffs" / "travel_private_v1" / "trip-site").exists()


def test_backend_status_failure_is_fail_closed_before_mutation(tmp_path: Path) -> None:
    runner = FakeRunner(status_ok=False)
    runtime.prepare_page_publication_handoff(prepare_body(), root=tmp_path, command_runner=runner)
    report = runtime.publish_static_page(publish_body(zip_bytes()), root=tmp_path, command_runner=runner, url_fetcher=fetcher)
    assert "reason=publication_backend_state_unavailable" in report
    assert not (tmp_path / "sites" / "travel_private_v1" / "trip-site" / "current").exists()
    assert not any("--set-path=" in part for command in runner.commands for part in command)


def test_existing_exact_route_is_refused(tmp_path: Path) -> None:
    runner = FakeRunner(existing_path=True)
    runtime.prepare_page_publication_handoff(prepare_body(), root=tmp_path, command_runner=runner)
    report = runtime.publish_static_page(publish_body(zip_bytes()), root=tmp_path, command_runner=runner, url_fetcher=fetcher)
    assert "reason=publication_path_already_configured" in report


def test_legacy_alias_is_profile_constrained(tmp_path: Path) -> None:
    from core import private_static_site_runtime as legacy
    runner = FakeRunner()
    body = "\n".join([
        "Operator Approval: prepare_private_static_site_handoff_v1",
        "Artifact ID: trip-site",
    ])
    report = legacy.prepare_private_static_site_handoff(body, root=tmp_path, command_runner=runner)
    assert "maintenance_task_id=prepare_private_static_site_handoff" in report
    assert "publication_profile_id=travel_private_v1" in report
