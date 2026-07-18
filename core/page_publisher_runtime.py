from __future__ import annotations

import base64
import binascii
import hashlib
import html.parser
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from core.page_publisher_profiles import PublicationProfile, get_profile

CANONICAL_PREPARE_TASK_ID = "prepare_page_publication_handoff"
CANONICAL_PUBLISH_TASK_ID = "publish_static_page"
LEGACY_PREPARE_TASK_ID = "prepare_private_static_site_handoff"
LEGACY_PUBLISH_TASK_ID = "deploy_private_static_site"
PREPARE_APPROVALS = frozenset({"prepare_page_publication_handoff_v1", "prepare_private_static_site_handoff_v1"})
PUBLISH_APPROVALS = frozenset({"publish_static_page_v1", "deploy_private_static_site_v1"})
ROOT_ENV = "SKELETON_PAGE_PUBLISHER_ROOT"
DEFAULT_BASE = Path("/home/agent/.local/share/skeleton")
DEFAULT_ROOT = DEFAULT_BASE / "page-publisher"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PAYLOAD_FENCE_RE = re.compile(r"(?ms)^```encrypted-payload-base64[ \t]*\n(?P<payload>[A-Za-z0-9+/=\s]+)\n```[ \t]*$")
RESULT_CERTIFICATE_FENCE_RE = re.compile(r"(?ms)^```result-certificate-pem[ \t]*\n(?P<cert>-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----)\n```[ \t]*$")
PRIVATE_KEY_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)
NESTED_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
OPENSSL_TIMEOUT = 25
TAILSCALE_TIMEOUT = 15
HTTPS_TIMEOUT = 15
MAX_TAILSCALE_STATUS_BYTES = 128 * 1024
MAX_TAILSCALE_STATUS_DEPTH = 12
MAX_TAILSCALE_STATUS_NODES = 2048

CommandRunner = Callable[[list[str], Path | None, int], tuple[int, str]]
UrlFetcher = Callable[[str, int], tuple[int, bytes]]


@dataclass(frozen=True)
class PublicationRequest:
    profile_id: str
    page_id: str
    operator_approval: str | None
    encrypted_sha256: str | None = None
    plaintext_zip_sha256: str | None = None
    result_certificate_der_sha256: str | None = None
    result_certificate_pem: str | None = None
    encrypted_payload_b64: str | None = None
    legacy_task: bool = False


class _AssetParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True
        attrs_map = {key.lower(): value for key, value in attrs}
        for attr in ("src", "href"):
            value = attrs_map.get(attr)
            if value:
                self.assets.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def prepare_page_publication_handoff(
    body: str,
    *,
    root: Path | None = None,
    command_runner: CommandRunner | None = None,
    force_legacy_task_id: bool = False,
) -> str:
    request, reason = _prepare_request(body)
    task_id = LEGACY_PREPARE_TASK_ID if force_legacy_task_id or (request and request.legacy_task) else CANONICAL_PREPARE_TASK_ID
    if reason is not None or request is None:
        return _report("BLOCKED", task_id, [f"reason={reason or 'invalid_request'}"])
    if request.operator_approval not in PREPARE_APPROVALS:
        return _report("BLOCKED", task_id, ["reason=missing_operator_approval"])
    profile = get_profile(request.profile_id)
    if profile is None:
        return _report("BLOCKED", task_id, ["reason=unknown_publication_profile"])
    resolved_root, root_reason = _private_root(root)
    if root_reason or resolved_root is None:
        return _report("BLOCKED", task_id, [f"reason={root_reason or 'invalid_private_root'}"])

    handoff = _handoff_path(resolved_root, profile, request.page_id)
    key_path = handoff / "private_key.pem"
    cert_path = handoff / "certificate.pem"
    runner = command_runner or _subprocess_runner
    try:
        _ensure_private_root(resolved_root)
        handoff.mkdir(mode=0o700, parents=True, exist_ok=False)
        code, _ = runner(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:4096",
                "-keyout", str(key_path), "-out", str(cert_path),
                "-days", "1", "-nodes", "-sha256",
                "-subj", f"/CN=skeleton-page-{profile.profile_id}-{request.page_id}",
            ],
            None,
            OPENSSL_TIMEOUT,
        )
        if code != 0:
            raise RuntimeError("openssl_keygen_failed")
        key_path.chmod(0o600)
        cert_path.chmod(0o644)
        cert_pem, fingerprint, expires_at = _validated_public_certificate(cert_path, runner)
        _probe_certificate(handoff, cert_path, key_path, runner)
    except FileExistsError:
        return _report("BLOCKED", task_id, [f"artifact_id={request.page_id}", "reason=handoff_already_exists"])
    except Exception:
        shutil.rmtree(handoff, ignore_errors=True)
        return _report("BLOCKED", task_id, ["reason=prepare_failed"])

    return _report(
        "DONE",
        task_id,
        [
            f"artifact_id={request.page_id}",
            f"publication_profile_id={profile.profile_id}",
            f"handoff_ref=handoff-{profile.profile_id}-{request.page_id}",
            f"certificate_sha256_fingerprint={fingerprint}",
            f"expires_at={expires_at}",
            "durable_handoff_status=DURABLE_HANDOFF_READY",
            "public_safe_report_ok=true",
        ],
        public_certificate_pem=cert_pem,
    )


def publish_static_page(
    body: str,
    *,
    root: Path | None = None,
    command_runner: CommandRunner | None = None,
    url_fetcher: UrlFetcher | None = None,
    force_legacy_task_id: bool = False,
) -> str:
    request, reason = _publish_request(body)
    task_id = LEGACY_PUBLISH_TASK_ID if force_legacy_task_id or (request and request.legacy_task) else CANONICAL_PUBLISH_TASK_ID
    if reason is not None or request is None:
        return _report("BLOCKED", task_id, [f"reason={reason or 'invalid_request'}"])
    if request.operator_approval not in PUBLISH_APPROVALS:
        return _report("BLOCKED", task_id, ["reason=missing_operator_approval"])
    profile = get_profile(request.profile_id)
    if profile is None:
        return _report("BLOCKED", task_id, ["reason=unknown_publication_profile"])
    resolved_root, root_reason = _private_root(root)
    if root_reason or resolved_root is None:
        return _report("BLOCKED", task_id, [f"reason={root_reason or 'invalid_private_root'}"])

    runner = command_runner or _subprocess_runner
    fetcher = url_fetcher or _urllib_fetcher
    url_path = profile.url_path(request.page_id)
    handoff = _handoff_path(resolved_root, profile, request.page_id)
    key_path = handoff / "private_key.pem"
    cert_path = handoff / "certificate.pem"
    site_root = resolved_root / "sites" / profile.profile_id / request.page_id
    current = site_root / "current"
    metadata_path = site_root / "deployment.json"
    tmp_dir: Path | None = None
    previous_current: Path | None = None
    prior_metadata: bytes | None = None
    prior_metadata_existed = False
    route_created = False
    committed = False
    ciphertext_hash_match = False
    plaintext_hash_match = False
    asset_count = 0
    cleanup_status = "not_started"

    try:
        _ensure_private_root(resolved_root)
        if not handoff.is_dir() or not key_path.is_file() or not cert_path.is_file():
            return _blocked(task_id, request, False, False, "handoff_missing")
        ciphertext = _strict_b64(request.encrypted_payload_b64 or "")
        if len(ciphertext) > profile.max_inline_ciphertext_bytes:
            return _blocked(task_id, request, False, False, "inline_payload_too_large")
        ciphertext_hash_match = hashlib.sha256(ciphertext).hexdigest() == request.encrypted_sha256
        if not ciphertext_hash_match:
            return _blocked(task_id, request, False, False, "ciphertext_sha256_mismatch")

        tmp_dir = Path(tempfile.mkdtemp(prefix="page-publisher-", dir=resolved_root))
        tmp_dir.chmod(0o700)
        cms_path = tmp_dir / "payload.cms"
        zip_path = tmp_dir / "payload.zip"
        cms_path.write_bytes(ciphertext)
        code, _ = runner(
            ["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(cms_path), "-recip", str(cert_path), "-inkey", str(key_path), "-out", str(zip_path)],
            None,
            OPENSSL_TIMEOUT,
        )
        if code != 0:
            return _blocked(task_id, request, True, False, "cms_decrypt_failed")
        zip_bytes = zip_path.read_bytes()
        plaintext_hash_match = hashlib.sha256(zip_bytes).hexdigest() == request.plaintext_zip_sha256
        if not plaintext_hash_match:
            return _blocked(task_id, request, True, False, "plaintext_zip_sha256_mismatch")

        result_cert_path = tmp_dir / "result-certificate.pem"
        result_der_path = tmp_dir / "result-certificate.der"
        result_cert_path.write_text(request.result_certificate_pem or "", encoding="ascii")
        result_der = _validate_result_certificate(result_cert_path, result_der_path, request.result_certificate_der_sha256 or "", runner)

        extract_root = tmp_dir / "extract"
        publish_source = _validate_and_extract_zip(zip_path, extract_root, profile)
        asset_count = _referenced_local_assets(publish_source)[1]

        status, status_reason = _tailscale_serve_status(runner)
        if status_reason is not None or status is None:
            return _blocked(task_id, request, True, True, "publication_backend_state_unavailable")
        configured, bounded = _tailscale_path_configured(status, url_path)
        if not bounded:
            return _blocked(task_id, request, True, True, "publication_backend_state_unavailable")
        if configured:
            return _blocked(task_id, request, True, True, "publication_path_already_configured")

        prior_metadata_existed = metadata_path.exists()
        prior_metadata = metadata_path.read_bytes() if prior_metadata_existed else None
        publish_dir, previous_current = _publish_site(publish_source, current)
        code, _ = _set_tailscale_path(runner, url_path, publish_dir)
        if code != 0:
            _restore_published_site(current, previous_current)
            return _blocked(task_id, request, True, True, "publication_backend_set_failed")
        route_created = True

        dns_name = _tailscale_magic_dns_name(runner)
        if dns_name is None:
            raise RuntimeError("publication_backend_identity_unavailable")
        private_url = f"https://{dns_name}{url_path}"
        ok, asset_count = _verify_deployed_site(private_url, publish_dir, fetcher)
        if not ok:
            raise RuntimeError("https_verification_failed")

        result_json = _result_payload_json(
            private_url=private_url,
            profile_id=profile.profile_id,
            page_id=request.page_id,
            verification_status="done",
            asset_count=asset_count,
            ciphertext_hash_match=ciphertext_hash_match,
            plaintext_hash_match=plaintext_hash_match,
            cleanup_status="secrets_removed",
        )
        result_plaintext_path = tmp_dir / "result.json"
        result_ciphertext_path = tmp_dir / "result.cms"
        result_plaintext_path.write_text(result_json, encoding="utf-8")
        code, _ = runner(
            ["openssl", "cms", "-encrypt", "-binary", "-aes256", "-outform", "DER", "-in", str(result_plaintext_path), "-out", str(result_ciphertext_path), str(result_cert_path)],
            None,
            OPENSSL_TIMEOUT,
        )
        if code != 0:
            raise RuntimeError("result_encrypt_failed")
        encrypted_result = result_ciphertext_path.read_bytes()
        encrypted_result_b64 = base64.b64encode(encrypted_result).decode("ascii")
        encrypted_result_sha256 = hashlib.sha256(encrypted_result).hexdigest()

        _write_deployment_metadata_atomic(
            metadata_path,
            profile_id=profile.profile_id,
            page_id=request.page_id,
            url_path=url_path,
            asset_count=asset_count,
            package_sha256=request.plaintext_zip_sha256 or "",
        )

        try:
            shutil.rmtree(handoff)
        except Exception as exc:
            raise RuntimeError("handoff_cleanup_failed") from exc
        committed = True
        cleanup_status = "secrets_removed"

        if previous_current is not None and previous_current.exists():
            try:
                shutil.rmtree(previous_current)
            except Exception:
                cleanup_status = "committed_cleanup_pending"

        return _report(
            "DONE",
            task_id,
            [
                f"artifact_id={request.page_id}",
                f"publication_profile_id={profile.profile_id}",
                f"ciphertext_sha256_match={str(ciphertext_hash_match).lower()}",
                f"plaintext_zip_sha256_match={str(plaintext_hash_match).lower()}",
                f"result_certificate_der_sha256_match={str(hashlib.sha256(result_der).hexdigest() == request.result_certificate_der_sha256).lower()}",
                f"asset_count={asset_count}",
                "serve_private=true",
                "verification_status=done",
                f"cleanup_status={cleanup_status}",
                f"encrypted_result_cms_sha256={encrypted_result_sha256}",
                f"encrypted_result_cms_bytes={len(encrypted_result)}",
                "status_token=DONE",
            ],
            encrypted_result_cms_b64=encrypted_result_b64,
        )
    except (binascii.Error, ValueError) as exc:
        reason_text = str(exc)
        reason = reason_text if re.fullmatch(r"[a-z0-9_]{3,80}", reason_text) else "invalid_payload"
        if not committed:
            _rollback_before_commit(runner, url_path, route_created, current, previous_current, metadata_path, prior_metadata_existed, prior_metadata)
        return _blocked(task_id, request, ciphertext_hash_match, plaintext_hash_match, reason)
    except Exception as exc:
        reason_text = str(exc)
        reason = reason_text if re.fullmatch(r"[a-z0-9_]{3,80}", reason_text) else "publication_failed"
        if not committed:
            _rollback_before_commit(runner, url_path, route_created, current, previous_current, metadata_path, prior_metadata_existed, prior_metadata)
        return _blocked(task_id, request, ciphertext_hash_match, plaintext_hash_match, reason)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _prepare_request(body: str) -> tuple[PublicationRequest | None, str | None]:
    profile_id = _body_field(body, "Publication Profile ID")
    page_id = _body_field(body, "Page ID")
    legacy = False
    if profile_id is None and _body_field(body, "Artifact ID") is not None:
        profile_id = "travel_private_v1"
        page_id = _body_field(body, "Artifact ID")
        legacy = True
    approval = _body_field(body, "Operator Approval")
    if profile_id is None or get_profile(profile_id) is None:
        return None, "unknown_publication_profile"
    profile = get_profile(profile_id)
    assert profile is not None
    if page_id is None or not profile.validate_page_id(page_id):
        return None, "invalid_page_id"
    return PublicationRequest(profile_id=profile_id, page_id=page_id, operator_approval=approval, legacy_task=legacy), None


def _publish_request(body: str) -> tuple[PublicationRequest | None, str | None]:
    base, reason = _prepare_request(body)
    if reason or base is None:
        return None, reason
    profile = get_profile(base.profile_id)
    assert profile is not None
    legacy_path = _body_field(body, "URL Path")
    if legacy_path is not None and legacy_path != profile.url_path(base.page_id):
        return None, "publication_path_profile_mismatch"
    encrypted_hash = _body_field(body, "Encrypted Payload SHA-256")
    plaintext_hash = _body_field(body, "Plaintext ZIP SHA-256")
    result_hash = _body_field(body, "Result Certificate DER SHA-256")
    payload_match = PAYLOAD_FENCE_RE.search(body or "")
    cert_match = RESULT_CERTIFICATE_FENCE_RE.search(body or "")
    if encrypted_hash is None or SHA256_RE.fullmatch(encrypted_hash) is None:
        return None, "invalid_ciphertext_sha256"
    if plaintext_hash is None or SHA256_RE.fullmatch(plaintext_hash) is None:
        return None, "invalid_plaintext_zip_sha256"
    if result_hash is None or SHA256_RE.fullmatch(result_hash) is None:
        return None, "invalid_result_certificate_der_sha256"
    if cert_match is None:
        return None, "missing_result_certificate"
    cert = cert_match.group("cert")
    if any(marker in cert for marker in PRIVATE_KEY_MARKERS):
        return None, "result_certificate_contains_private_key_marker"
    if payload_match is None:
        return None, "missing_encrypted_payload"
    return PublicationRequest(
        profile_id=base.profile_id,
        page_id=base.page_id,
        operator_approval=base.operator_approval,
        encrypted_sha256=encrypted_hash,
        plaintext_zip_sha256=plaintext_hash,
        result_certificate_der_sha256=result_hash,
        result_certificate_pem=cert,
        encrypted_payload_b64=payload_match.group("payload"),
        legacy_task=base.legacy_task,
    ), None


def _body_field(body: str, field: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(field)}:\s*(?P<value>.+?)\s*$", body or "", re.MULTILINE)
    return match.group("value").strip() if match else None


def _private_root(root: Path | None) -> tuple[Path | None, str | None]:
    if root is not None:
        return root.expanduser().resolve(strict=False), None
    configured = os.environ.get(ROOT_ENV)
    candidate = Path(configured).expanduser() if configured else DEFAULT_ROOT
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(DEFAULT_BASE.resolve(strict=False))
    except ValueError:
        return None, "private_root_outside_allowed_base"
    return resolved, None


def _ensure_private_root(root: Path) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    for child in ("handoffs", "sites"):
        path = root / child
        path.mkdir(mode=0o700, exist_ok=True)
        path.chmod(0o700)


def _handoff_path(root: Path, profile: PublicationProfile, page_id: str) -> Path:
    return root / "handoffs" / profile.profile_id / page_id


def _validated_public_certificate(cert_path: Path, runner: CommandRunner) -> tuple[str, str, str]:
    code, output = runner(["openssl", "x509", "-in", str(cert_path), "-noout", "-fingerprint", "-sha256", "-enddate"], None, OPENSSL_TIMEOUT)
    if code != 0:
        raise RuntimeError("certificate_parse_failed")
    cert_pem = cert_path.read_text(encoding="ascii")
    if any(marker in cert_pem for marker in PRIVATE_KEY_MARKERS):
        raise RuntimeError("certificate_contains_private_key_marker")
    fingerprint = hashlib.sha256(cert_path.read_bytes()).hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    for line in output.splitlines():
        if line.lower().startswith("sha256 fingerprint="):
            fingerprint = line.split("=", 1)[1].replace(":", "").lower()
        elif line.startswith("notAfter="):
            expiry = re.sub(r"[^A-Za-z0-9_+,-]", "_", line.split("=", 1)[1])[:80]
    return cert_pem.strip(), fingerprint, expiry


def _probe_certificate(handoff: Path, cert_path: Path, key_path: Path, runner: CommandRunner) -> None:
    probe_in, probe_cms, probe_out = handoff / "probe.txt", handoff / "probe.cms", handoff / "probe.out"
    try:
        probe_in.write_text("skeleton-page-publisher-probe\n", encoding="ascii")
        code, _ = runner(["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-in", str(probe_in), "-out", str(probe_cms), str(cert_path)], None, OPENSSL_TIMEOUT)
        if code != 0:
            raise RuntimeError("probe_encrypt_failed")
        code, _ = runner(["openssl", "cms", "-decrypt", "-binary", "-inform", "DER", "-in", str(probe_cms), "-recip", str(cert_path), "-inkey", str(key_path), "-out", str(probe_out)], None, OPENSSL_TIMEOUT)
        if code != 0 or probe_out.read_bytes() != probe_in.read_bytes():
            raise RuntimeError("probe_decrypt_failed")
    finally:
        for path in (probe_in, probe_cms, probe_out):
            path.unlink(missing_ok=True)


def _strict_b64(payload: str) -> bytes:
    return base64.b64decode("".join(payload.split()).encode("ascii"), validate=True)


def _validate_and_extract_zip(zip_path: Path, extract_root: Path, profile: PublicationProfile) -> Path:
    extract_root.mkdir(mode=0o700)
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > profile.max_entries:
            raise ValueError("zip_too_many_entries")
        top_levels = {PurePosixPath(info.filename).parts[0] for info in infos if info.filename and not info.is_dir()}
        for info in infos:
            name = info.filename
            if not name or "\\" in name:
                raise ValueError("zip_invalid_path")
            posix = PurePosixPath(name)
            if posix.is_absolute() or ".." in posix.parts or name in seen:
                raise ValueError("zip_traversal")
            seen.add(name)
            mode = (info.external_attr >> 16) & 0o777777
            file_type = mode & 0o170000
            if file_type in {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK}:
                raise ValueError("zip_unsafe_file_type")
            target = extract_root / Path(*posix.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            if mode & 0o111:
                raise ValueError("zip_executable_bit")
            suffix = posix.suffix.lower()
            if suffix not in profile.allowed_extensions:
                raise ValueError("zip_disallowed_extension")
            if suffix in NESTED_ARCHIVE_EXTENSIONS:
                raise ValueError("zip_nested_archive")
            total += info.file_size
            if total > profile.max_uncompressed_bytes:
                raise ValueError("zip_uncompressed_too_large")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            target.chmod(0o644)
    source = extract_root
    if len(top_levels) == 1:
        only = next(iter(top_levels))
        if (extract_root / only).is_dir():
            source = extract_root / only
    if not (source / "index.html").is_file():
        raise ValueError("zip_missing_index")
    for path in list(source.rglob("*.html")) + list(source.rglob("*.js")):
        content = path.read_text(encoding="utf-8", errors="ignore")
        if any(marker in content for marker in PRIVATE_KEY_MARKERS):
            raise ValueError("zip_private_key_marker")
    return source


def _referenced_local_assets(site_dir: Path) -> tuple[list[Path], int]:
    parser = _AssetParser()
    parser.feed((site_dir / "index.html").read_text(encoding="utf-8", errors="ignore"))
    local: list[Path] = []
    for ref in parser.assets:
        parsed = urllib.parse.urlparse(ref)
        if parsed.scheme or parsed.netloc or parsed.path.startswith("/") or not parsed.path:
            continue
        rel = PurePosixPath(parsed.path)
        if ".." in rel.parts:
            raise ValueError("zip_asset_traversal")
        target = site_dir / Path(*rel.parts)
        if not target.is_file():
            raise ValueError("zip_missing_asset")
        local.append(target)
    return local, len(local)


def _publish_site(source: Path, current: Path) -> tuple[Path, Path | None]:
    site_root = current.parent
    site_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = site_root / f".next-{uuid.uuid4().hex}"
    shutil.copytree(source, staging)
    for path in staging.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    previous: Path | None = None
    if current.exists():
        previous = site_root / f".previous-{uuid.uuid4().hex}"
        current.rename(previous)
    staging.rename(current)
    return current, previous


def _restore_published_site(current: Path, previous: Path | None) -> None:
    if current.exists():
        shutil.rmtree(current)
    if previous is not None and previous.exists():
        previous.rename(current)


def _tailscale_serve_status(runner: CommandRunner) -> tuple[dict[str, object] | None, str | None]:
    code, output = runner(["tailscale", "serve", "status", "--json"], None, TAILSCALE_TIMEOUT)
    if code != 0 or len(output.encode("utf-8", errors="ignore")) > MAX_TAILSCALE_STATUS_BYTES:
        return None, "unavailable"
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError:
        return None, "unavailable"
    return (parsed, None) if isinstance(parsed, dict) else (None, "unavailable")


def _tailscale_path_configured(status: dict[str, object], url_path: str) -> tuple[bool, bool]:
    remaining = MAX_TAILSCALE_STATUS_NODES
    bounded = True

    def visit(value: object, depth: int) -> bool:
        nonlocal remaining, bounded
        if depth > MAX_TAILSCALE_STATUS_DEPTH or remaining <= 0:
            bounded = False
            return False
        remaining -= 1
        if isinstance(value, dict):
            for key, child in value.items():
                if key == url_path:
                    return True
                if visit(child, depth + 1):
                    return True
        elif isinstance(value, list):
            for child in value:
                if visit(child, depth + 1):
                    return True
        return False

    configured = visit(status, 0)
    return configured, bounded


def _run_with_sudo_fallback(runner: CommandRunner, command: list[str]) -> tuple[int, str]:
    code, output = runner(command, None, TAILSCALE_TIMEOUT)
    if code == 0:
        return code, output
    if "permission" in output.lower() or "sudo" in output.lower():
        return runner(["sudo", "-n", *command], None, TAILSCALE_TIMEOUT)
    return code, output


def _set_tailscale_path(runner: CommandRunner, url_path: str, directory: Path) -> tuple[int, str]:
    return _run_with_sudo_fallback(runner, ["tailscale", "serve", "--bg", "--yes", "--https=443", f"--set-path={url_path}", str(directory.resolve(strict=False))])


def _remove_tailscale_path(runner: CommandRunner, url_path: str) -> tuple[int, str]:
    return _run_with_sudo_fallback(runner, ["tailscale", "serve", "--bg", "--yes", "--https=443", f"--set-path={url_path}", "off"])


def _tailscale_magic_dns_name(runner: CommandRunner) -> str | None:
    code, output = runner(["tailscale", "status", "--json"], None, TAILSCALE_TIMEOUT)
    if code != 0:
        return None
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError:
        return None
    self_node = parsed.get("Self") if isinstance(parsed, dict) else None
    dns = self_node.get("DNSName") if isinstance(self_node, dict) else None
    if not isinstance(dns, str):
        return None
    dns = dns.rstrip(".")
    return dns if re.fullmatch(r"[A-Za-z0-9.-]+", dns) and "." in dns else None


def _verify_deployed_site(url: str, site_dir: Path, fetcher: UrlFetcher) -> tuple[bool, int]:
    status, body = fetcher(url, HTTPS_TIMEOUT)
    if status != 200:
        return False, 0
    parser = _AssetParser()
    parser.feed(body.decode("utf-8", errors="ignore"))
    if not parser.title.strip():
        return False, 0
    assets, count = _referenced_local_assets(site_dir)
    for asset in assets:
        rel = asset.relative_to(site_dir).as_posix()
        asset_status, _ = fetcher(f"{url.rstrip('/')}/{rel}", HTTPS_TIMEOUT)
        if asset_status != 200:
            return False, count
    return True, count


def _validate_result_certificate(cert_path: Path, der_path: Path, expected_sha256: str, runner: CommandRunner) -> bytes:
    pem = cert_path.read_text(encoding="ascii")
    if any(marker in pem for marker in PRIVATE_KEY_MARKERS):
        raise ValueError("result_certificate_contains_private_key_marker")
    code, _ = runner(["openssl", "x509", "-in", str(cert_path), "-outform", "DER", "-out", str(der_path)], None, OPENSSL_TIMEOUT)
    if code != 0 or not der_path.is_file():
        raise ValueError("result_certificate_invalid")
    der = der_path.read_bytes()
    if hashlib.sha256(der).hexdigest() != expected_sha256:
        raise ValueError("result_certificate_der_sha256_mismatch")
    return der


def _write_deployment_metadata_atomic(path: Path, *, profile_id: str, page_id: str, url_path: str, asset_count: int, package_sha256: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "schema": "skeleton.page_publisher.deployment.v1",
        "profile_id": profile_id,
        "page_id": page_id,
        "url_path": url_path,
        "asset_count": asset_count,
        "package_sha256": package_sha256,
        "deployed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, sort_keys=True), encoding="ascii")
    temp.chmod(0o600)
    temp.replace(path)
    path.chmod(0o600)


def _restore_metadata(path: Path, existed: bool, content: bytes | None) -> None:
    if existed:
        assert content is not None
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.restore")
        temp.write_bytes(content)
        temp.chmod(0o600)
        temp.replace(path)
    else:
        path.unlink(missing_ok=True)


def _rollback_before_commit(runner: CommandRunner, url_path: str, route_created: bool, current: Path, previous: Path | None, metadata_path: Path, prior_metadata_existed: bool, prior_metadata: bytes | None) -> None:
    if route_created:
        _remove_tailscale_path(runner, url_path)
    _restore_published_site(current, previous)
    _restore_metadata(metadata_path, prior_metadata_existed, prior_metadata)


def _result_payload_json(*, private_url: str, profile_id: str, page_id: str, verification_status: str, asset_count: int, ciphertext_hash_match: bool, plaintext_hash_match: bool, cleanup_status: str) -> str:
    return json.dumps({
        "asset_count": asset_count,
        "ciphertext_sha256_match": ciphertext_hash_match,
        "cleanup_status": cleanup_status,
        "page_id": page_id,
        "plaintext_zip_sha256_match": plaintext_hash_match,
        "private_url": private_url,
        "profile_id": profile_id,
        "verification_status": verification_status,
    }, sort_keys=True, separators=(",", ":"))


def _urllib_fetcher(url: str, timeout: int) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return int(response.status), response.read(256 * 1024)


def _subprocess_runner(args: list[str], cwd: Path | None, timeout: int) -> tuple[int, str]:
    import subprocess
    result = subprocess.run(args, cwd=str(cwd) if cwd else None, check=False, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout + result.stderr


def _blocked(task_id: str, request: PublicationRequest, ciphertext_match: bool, plaintext_match: bool, reason: str) -> str:
    safe_reason = reason if re.fullmatch(r"[a-z0-9_]{3,80}", reason) else "publication_failed"
    return _report("BLOCKED", task_id, [
        f"artifact_id={request.page_id}",
        f"publication_profile_id={request.profile_id}",
        f"ciphertext_sha256_match={str(ciphertext_match).lower()}",
        f"plaintext_zip_sha256_match={str(plaintext_match).lower()}",
        f"reason={safe_reason}",
        "cleanup_status=not_completed",
    ])


def _report(status: str, task_id: str, lines: Iterable[str], *, public_certificate_pem: str | None = None, encrypted_result_cms_b64: str | None = None) -> str:
    heading = "DONE: Runner host maintenance task completed." if status == "DONE" else "BLOCKED: Runner host maintenance task did not complete."
    body = [heading, f"maintenance_task_id={task_id}", *_safe_lines(lines)]
    if public_certificate_pem is not None:
        body.extend(["public_certificate_pem_start", public_certificate_pem, "public_certificate_pem_end"])
    if encrypted_result_cms_b64 is not None:
        body.extend(["encrypted_result_cms_b64_start", encrypted_result_cms_b64, "encrypted_result_cms_b64_end"])
    body.append(f"success_criteria={'met' if status == 'DONE' else 'not_met'}")
    return "\n".join(body)


def _safe_lines(lines: Iterable[str]) -> list[str]:
    allowed_keys = {
        "artifact_id", "publication_profile_id", "handoff_ref", "certificate_sha256_fingerprint", "expires_at",
        "durable_handoff_status", "public_safe_report_ok", "reason", "ciphertext_sha256_match",
        "plaintext_zip_sha256_match", "result_certificate_der_sha256_match", "asset_count", "serve_private",
        "verification_status", "cleanup_status", "encrypted_result_cms_sha256", "encrypted_result_cms_bytes", "status_token",
    }
    safe: list[str] = []
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        normalized: list[str] = []
        for token in tokens:
            if token.count("=") != 1:
                normalized = []
                break
            key, value = token.split("=", 1)
            if key not in allowed_keys or re.fullmatch(r"[A-Za-z0-9._:+,@/#-]{1,180}", value) is None:
                normalized = []
                break
            if key != "cleanup_status" and any(marker in value.lower() for marker in ("secret", "token", "private_key", "password", "credential")):
                normalized = []
                break
            normalized.append(token)
        if normalized:
            safe.append(" ".join(normalized))
    return safe
