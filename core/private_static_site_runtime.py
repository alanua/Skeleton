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


PREPARE_TASK_ID = "prepare_private_static_site_handoff"
DEPLOY_TASK_ID = "deploy_private_static_site"
PREPARE_APPROVAL = "prepare_private_static_site_handoff_v1"
DEPLOY_APPROVAL = "deploy_private_static_site_v1"
ROOT_ENV = "SKELETON_PRIVATE_STATIC_SITE_ROOT"
DEFAULT_BASE = Path("/home/agent/.local/share/skeleton")
DEFAULT_ROOT = DEFAULT_BASE / "private-static-sites"
ARTIFACT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
URL_PATH_RE = re.compile(r"^/travel/[a-z0-9][a-z0-9-]{2,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PAYLOAD_FENCE_RE = re.compile(
    r"(?ms)^```encrypted-payload-base64[ \t]*\n(?P<payload>[A-Za-z0-9+/=\s]+)\n```[ \t]*$"
)
PRIVATE_KEY_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)
ALLOWED_EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".json",
    ".webmanifest",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".svg",
    ".ico",
    ".txt",
    ".md",
}
NESTED_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
MAX_CIPHERTEXT_BYTES = 2 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 5 * 1024 * 1024
MAX_ZIP_ENTRIES = 64
OPENSSL_TIMEOUT = 20
TAILSCALE_TIMEOUT = 12
HTTPS_TIMEOUT = 12

CommandRunner = Callable[[list[str], Path | None, int], tuple[int, str]]
UrlFetcher = Callable[[str, int], tuple[int, bytes]]


@dataclass(frozen=True)
class StaticSiteRequest:
    artifact_id: str
    operator_approval: str | None
    url_path: str | None = None
    encrypted_sha256: str | None = None
    plaintext_zip_sha256: str | None = None
    encrypted_payload_b64: str | None = None


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


def prepare_private_static_site_handoff(
    body: str,
    *,
    root: Path | None = None,
    command_runner: CommandRunner | None = None,
) -> str:
    request, reason = _prepare_request(body)
    task_id = PREPARE_TASK_ID
    if reason is not None or request is None:
        return _report("BLOCKED", task_id, [f"reason={reason or 'invalid_request'}"])
    if request.operator_approval != PREPARE_APPROVAL:
        return _report("BLOCKED", task_id, ["reason=missing_operator_approval"])
    resolved_root, root_reason = _private_root(root)
    if root_reason is not None or resolved_root is None:
        return _report("BLOCKED", task_id, [f"reason={root_reason or 'invalid_private_root'}"])

    handoff = resolved_root / "handoffs" / request.artifact_id
    key_path = handoff / "private_key.pem"
    cert_path = handoff / "certificate.pem"
    runner = command_runner or _subprocess_runner
    try:
        _ensure_private_root(resolved_root)
        handoff.mkdir(mode=0o700, parents=True, exist_ok=False)
        code, _ = runner(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:4096",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-days",
                "1",
                "-nodes",
                "-subj",
                f"/CN=skeleton-private-static-site-{request.artifact_id}",
            ],
            None,
            OPENSSL_TIMEOUT,
        )
        if code != 0:
            raise RuntimeError("openssl_keygen_failed")
        key_path.chmod(0o600)
        cert_path.chmod(0o644)
        cert_pem, fingerprint, expires_at = _validated_public_certificate(
            cert_path, runner
        )
        _probe_certificate(handoff, cert_path, key_path, runner)
    except FileExistsError:
        return _report(
            "BLOCKED",
            task_id,
            [f"artifact_id={request.artifact_id}", "reason=handoff_already_exists"],
        )
    except Exception:
        shutil.rmtree(handoff, ignore_errors=True)
        return _report("BLOCKED", task_id, ["reason=prepare_failed"])

    return _report(
        "DONE",
        task_id,
        [
            f"artifact_id={request.artifact_id}",
            f"handoff_ref=handoff-{request.artifact_id}",
            f"certificate_sha256_fingerprint={fingerprint}",
            f"expires_at={expires_at}",
            "durable_handoff_status=DURABLE_HANDOFF_READY",
            "public_safe_report_ok=true",
        ],
        public_certificate_pem=cert_pem,
    )


def deploy_private_static_site(
    body: str,
    *,
    root: Path | None = None,
    command_runner: CommandRunner | None = None,
    url_fetcher: UrlFetcher | None = None,
) -> str:
    request, reason = _deploy_request(body)
    task_id = DEPLOY_TASK_ID
    if reason is not None or request is None:
        return _report("BLOCKED", task_id, [f"reason={reason or 'invalid_request'}"])
    if request.operator_approval != DEPLOY_APPROVAL:
        return _report("BLOCKED", task_id, ["reason=missing_operator_approval"])
    resolved_root, root_reason = _private_root(root)
    if root_reason is not None or resolved_root is None:
        return _report("BLOCKED", task_id, [f"reason={root_reason or 'invalid_private_root'}"])

    runner = command_runner or _subprocess_runner
    fetcher = url_fetcher or _urllib_fetcher
    handoff = resolved_root / "handoffs" / request.artifact_id
    key_path = handoff / "private_key.pem"
    cert_path = handoff / "certificate.pem"
    current = resolved_root / "sites" / request.artifact_id / "current"
    tmp_dir: Path | None = None
    serve_mutated = False
    asset_count = 0
    cleanup_status = "not_started"
    ciphertext_hash_match = False
    plaintext_hash_match = False
    try:
        _ensure_private_root(resolved_root)
        if not handoff.is_dir() or not key_path.is_file() or not cert_path.is_file():
            return _report("BLOCKED", task_id, ["reason=handoff_missing"])
        ciphertext = _strict_b64(request.encrypted_payload_b64 or "")
        if len(ciphertext) > MAX_CIPHERTEXT_BYTES:
            return _report("BLOCKED", task_id, ["reason=ciphertext_too_large"])
        ciphertext_hash_match = hashlib.sha256(ciphertext).hexdigest() == request.encrypted_sha256
        if not ciphertext_hash_match:
            return _deploy_blocked(request.artifact_id, ciphertext_hash_match, False, "ciphertext_sha256_mismatch")

        tmp_dir = Path(tempfile.mkdtemp(prefix="private-static-site-", dir=resolved_root))
        tmp_dir.chmod(0o700)
        cms_path = tmp_dir / "payload.cms"
        zip_path = tmp_dir / "payload.zip"
        cms_path.write_bytes(ciphertext)
        code, _ = runner(
            [
                "openssl",
                "cms",
                "-decrypt",
                "-inform",
                "DER",
                "-in",
                str(cms_path),
                "-recip",
                str(cert_path),
                "-inkey",
                str(key_path),
                "-out",
                str(zip_path),
            ],
            None,
            OPENSSL_TIMEOUT,
        )
        if code != 0:
            return _deploy_blocked(request.artifact_id, True, False, "cms_decrypt_failed")
        zip_bytes = zip_path.read_bytes()
        plaintext_hash_match = hashlib.sha256(zip_bytes).hexdigest() == request.plaintext_zip_sha256
        if not plaintext_hash_match:
            return _deploy_blocked(request.artifact_id, True, False, "plaintext_zip_sha256_mismatch")

        extract_root = tmp_dir / "extract"
        publish_source, entries = _validate_and_extract_zip(zip_path, extract_root)
        asset_count = _referenced_local_assets(publish_source)[1]
        previous_status = _tailscale_serve_status(runner)
        publish_dir = _publish_site(publish_source, current)
        code, _ = _set_tailscale_path(runner, request.url_path or "", publish_dir)
        if code != 0:
            return _deploy_blocked(request.artifact_id, True, True, "tailscale_serve_set_failed")
        serve_mutated = True
        dns_name = _tailscale_magic_dns_name(runner)
        if dns_name is None:
            _rollback_tailscale_path(runner, request.url_path or "", previous_status)
            return _deploy_blocked(request.artifact_id, True, True, "tailscale_magicdns_missing")
        url = f"https://{dns_name}{request.url_path}"
        ok, asset_count = _verify_deployed_site(url, publish_dir, fetcher)
        if not ok:
            _rollback_tailscale_path(runner, request.url_path or "", previous_status)
            return _deploy_blocked(request.artifact_id, True, True, "https_verification_failed")
        for fragment in ("#probe", "#a/b", "#x-y"):
            if urllib.parse.urlparse(f"{url}{fragment}")._replace(fragment="").geturl() != url:
                _rollback_tailscale_path(runner, request.url_path or "", previous_status)
                return _deploy_blocked(request.artifact_id, True, True, "fragment_verification_failed")
        shutil.rmtree(handoff)
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir = None
        _write_deployment_metadata(current.parent, request.artifact_id, request.url_path or "", asset_count)
        cleanup_status = "secrets_removed"
        return _report(
            "DONE",
            task_id,
            [
                f"artifact_id={request.artifact_id}",
                f"private_tailscale_url={url}",
                f"ciphertext_sha256_match={str(ciphertext_hash_match).lower()}",
                f"plaintext_zip_sha256_match={str(plaintext_hash_match).lower()}",
                f"asset_count={asset_count}",
                "serve_private=true",
                "verification_status=done",
                f"cleanup_status={cleanup_status}",
                "status_token=DONE",
            ],
        )
    except (binascii.Error, ValueError) as exc:
        reason = str(exc) if str(exc).startswith("zip_") else "invalid_payload"
        return _deploy_blocked(request.artifact_id if request else "invalid", ciphertext_hash_match, plaintext_hash_match, reason)
    except Exception:
        if serve_mutated and request is not None:
            _rollback_tailscale_path(runner, request.url_path or "", None)
        return _deploy_blocked(request.artifact_id if request else "invalid", ciphertext_hash_match, plaintext_hash_match, "deploy_failed")
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _prepare_request(body: str) -> tuple[StaticSiteRequest | None, str | None]:
    artifact_id = _body_field(body, "Artifact ID")
    approval = _body_field(body, "Operator Approval")
    if artifact_id is None:
        return None, "missing_artifact_id"
    if ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
        return None, "invalid_artifact_id"
    return StaticSiteRequest(artifact_id=artifact_id, operator_approval=approval), None


def _deploy_request(body: str) -> tuple[StaticSiteRequest | None, str | None]:
    artifact_id = _body_field(body, "Artifact ID")
    url_path = _body_field(body, "URL Path")
    encrypted_hash = _body_field(body, "Encrypted Payload SHA-256")
    plaintext_hash = _body_field(body, "Plaintext ZIP SHA-256")
    approval = _body_field(body, "Operator Approval")
    payload_match = PAYLOAD_FENCE_RE.search(body or "")
    if artifact_id is None:
        return None, "missing_artifact_id"
    if ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
        return None, "invalid_artifact_id"
    if url_path is None or URL_PATH_RE.fullmatch(url_path) is None:
        return None, "invalid_url_path"
    if encrypted_hash is None or SHA256_RE.fullmatch(encrypted_hash) is None:
        return None, "invalid_ciphertext_sha256"
    if plaintext_hash is None or SHA256_RE.fullmatch(plaintext_hash) is None:
        return None, "invalid_plaintext_zip_sha256"
    if payload_match is None:
        return None, "missing_encrypted_payload"
    return (
        StaticSiteRequest(
            artifact_id=artifact_id,
            operator_approval=approval,
            url_path=url_path,
            encrypted_sha256=encrypted_hash,
            plaintext_zip_sha256=plaintext_hash,
            encrypted_payload_b64=payload_match.group("payload"),
        ),
        None,
    )


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


def _validated_public_certificate(
    cert_path: Path, runner: CommandRunner
) -> tuple[str, str, str]:
    code, output = runner(["openssl", "x509", "-in", str(cert_path), "-noout", "-fingerprint", "-sha256", "-enddate"], None, OPENSSL_TIMEOUT)
    if code != 0:
        raise RuntimeError("certificate_parse_failed")
    cert_pem = cert_path.read_text(encoding="ascii")
    if any(marker in cert_pem for marker in PRIVATE_KEY_MARKERS):
        raise RuntimeError("certificate_contains_private_key_marker")
    if "-----BEGIN CERTIFICATE-----" not in cert_pem or "-----END CERTIFICATE-----" not in cert_pem:
        raise RuntimeError("certificate_not_pem")
    fingerprint = hashlib.sha256(cert_path.read_bytes()).hexdigest()
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    for line in output.splitlines():
        if line.startswith("sha256 Fingerprint=") or line.startswith("SHA256 Fingerprint="):
            fingerprint = line.split("=", 1)[1].replace(":", "").lower()
        if line.startswith("notAfter="):
            expiry = _safe_symbol(line.split("=", 1)[1]) or expiry
    return cert_pem.strip(), fingerprint, expiry


def _probe_certificate(handoff: Path, cert_path: Path, key_path: Path, runner: CommandRunner) -> None:
    probe_in = handoff / "probe.txt"
    probe_cms = handoff / "probe.cms"
    probe_out = handoff / "probe.out"
    try:
        probe_in.write_text("skeleton-private-static-site-probe\n", encoding="ascii")
        code, _ = runner(["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-out", str(probe_cms), str(cert_path), str(probe_in)], None, OPENSSL_TIMEOUT)
        if code != 0:
            raise RuntimeError("probe_encrypt_failed")
        code, _ = runner(["openssl", "cms", "-decrypt", "-inform", "DER", "-in", str(probe_cms), "-recip", str(cert_path), "-inkey", str(key_path), "-out", str(probe_out)], None, OPENSSL_TIMEOUT)
        if code != 0 or probe_out.read_text(encoding="ascii") != probe_in.read_text(encoding="ascii"):
            raise RuntimeError("probe_decrypt_failed")
    finally:
        for path in (probe_in, probe_cms, probe_out):
            path.unlink(missing_ok=True)


def _strict_b64(payload: str) -> bytes:
    compact = "".join(payload.split())
    return base64.b64decode(compact.encode("ascii"), validate=True)


def _validate_and_extract_zip(zip_path: Path, extract_root: Path) -> tuple[Path, list[str]]:
    extract_root.mkdir(mode=0o700)
    seen: set[str] = set()
    total = 0
    entries: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError("zip_too_many_entries")
        top_levels = {PurePosixPath(info.filename).parts[0] for info in infos if info.filename and not info.filename.endswith("/")}
        for info in infos:
            name = info.filename
            if not name or "\\" in name:
                raise ValueError("zip_invalid_path")
            posix = PurePosixPath(name)
            if posix.is_absolute() or ".." in posix.parts:
                raise ValueError("zip_traversal")
            if name in seen:
                raise ValueError("zip_duplicate_entry")
            seen.add(name)
            mode = (info.external_attr >> 16) & 0o777777
            file_type = mode & 0o170000
            if file_type in {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK}:
                raise ValueError("zip_unsafe_file_type")
            if mode & 0o111:
                raise ValueError("zip_executable_bit")
            if info.is_dir():
                continue
            suffix = posix.suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                raise ValueError("zip_disallowed_extension")
            if suffix in NESTED_ARCHIVE_EXTENSIONS:
                raise ValueError("zip_nested_archive")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("zip_uncompressed_too_large")
            target = extract_root / Path(*posix.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            target.chmod(0o644)
            entries.append(name)
    source = extract_root
    if len(top_levels) == 1:
        only = next(iter(top_levels))
        if (extract_root / only).is_dir():
            source = extract_root / only
    if not (source / "index.html").is_file():
        raise ValueError("zip_missing_index")
    for path in list(source.rglob("*.html")) + list(source.rglob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(marker in text for marker in PRIVATE_KEY_MARKERS):
            raise ValueError("zip_private_key_marker")
    return source, entries


def _referenced_local_assets(site_dir: Path) -> tuple[list[Path], int]:
    parser = _AssetParser()
    index = site_dir / "index.html"
    parser.feed(index.read_text(encoding="utf-8", errors="ignore"))
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


def _publish_site(source: Path, current: Path) -> Path:
    site_root = current.parent
    staging = site_root / f".next-{uuid.uuid4().hex}"
    site_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copytree(source, staging)
    for path in staging.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    previous = site_root / f".previous-{uuid.uuid4().hex}"
    if current.exists():
        current.rename(previous)
    staging.rename(current)
    if previous.exists():
        shutil.rmtree(previous)
    return current


def _tailscale_serve_status(runner: CommandRunner) -> dict[str, object] | None:
    code, output = runner(["tailscale", "serve", "status", "--json"], None, TAILSCALE_TIMEOUT)
    if code != 0:
        return None
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _set_tailscale_path(runner: CommandRunner, url_path: str, directory: Path) -> tuple[int, str]:
    command = ["tailscale", "serve", "--bg", "--yes", "--https=443", f"--set-path={url_path}", str(directory.resolve(strict=False))]
    code, output = runner(command, None, TAILSCALE_TIMEOUT)
    if code == 0:
        return code, output
    if "permission" in output.lower() or "sudo" in output.lower():
        return runner(["sudo", "-n", *command], None, TAILSCALE_TIMEOUT)
    return code, output


def _rollback_tailscale_path(runner: CommandRunner, url_path: str, _status: dict[str, object] | None) -> None:
    runner(["tailscale", "serve", "--bg", "--yes", "--https=443", f"--set-path={url_path}", "off"], None, TAILSCALE_TIMEOUT)


def _tailscale_magic_dns_name(runner: CommandRunner) -> str | None:
    code, output = runner(["tailscale", "status", "--json"], None, TAILSCALE_TIMEOUT)
    if code != 0:
        return None
    try:
        parsed = json.loads(output or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    self_node = parsed.get("Self")
    dns = self_node.get("DNSName") if isinstance(self_node, dict) else None
    if not isinstance(dns, str):
        return None
    dns = dns.rstrip(".")
    if re.fullmatch(r"[A-Za-z0-9.-]+", dns) is None or "." not in dns:
        return None
    return dns


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


def _write_deployment_metadata(site_root: Path, artifact_id: str, url_path: str, asset_count: int) -> None:
    metadata = {
        "schema": "skeleton.private_static_site_deployment.v1",
        "artifact_id": artifact_id,
        "url_path": url_path,
        "asset_count": asset_count,
        "deployed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    (site_root / "deployment.json").write_text(json.dumps(metadata, sort_keys=True), encoding="ascii")
    (site_root / "deployment.json").chmod(0o644)


def _urllib_fetcher(url: str, timeout: int) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return int(response.status), response.read(256 * 1024)


def _subprocess_runner(args: list[str], cwd: Path | None, timeout: int) -> tuple[int, str]:
    import subprocess

    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout + result.stderr


def _safe_symbol(value: str) -> str | None:
    normalized = value.strip().replace(" ", "_").replace(":", "")
    if re.fullmatch(r"[A-Za-z0-9_+,-]{1,80}", normalized):
        return normalized
    return None


def _deploy_blocked(
    artifact_id: str,
    ciphertext_match: bool,
    plaintext_match: bool,
    reason: str,
) -> str:
    safe_reason = reason if re.fullmatch(r"[a-z0-9_]{3,80}", reason) else "deploy_failed"
    return _report(
        "BLOCKED",
        DEPLOY_TASK_ID,
        [
            f"artifact_id={artifact_id}",
            f"ciphertext_sha256_match={str(ciphertext_match).lower()}",
            f"plaintext_zip_sha256_match={str(plaintext_match).lower()}",
            f"reason={safe_reason}",
            "cleanup_status=not_completed",
        ],
    )


def _report(
    status: str,
    task_id: str,
    lines: Iterable[str],
    *,
    public_certificate_pem: str | None = None,
) -> str:
    heading = (
        "DONE: Runner host maintenance task completed."
        if status == "DONE"
        else "BLOCKED: Runner host maintenance task did not complete."
    )
    body = [heading, f"maintenance_task_id={task_id}", *_safe_lines(lines)]
    if public_certificate_pem is not None:
        body.extend(["public_certificate_pem_start", public_certificate_pem, "public_certificate_pem_end"])
    body.append(f"success_criteria={'met' if status == 'DONE' else 'not_met'}")
    return "\n".join(body)


def _safe_lines(lines: Iterable[str]) -> list[str]:
    safe: list[str] = []
    allowed_keys = {
        "artifact_id",
        "handoff_ref",
        "certificate_sha256_fingerprint",
        "expires_at",
        "durable_handoff_status",
        "public_safe_report_ok",
        "reason",
        "private_tailscale_url",
        "ciphertext_sha256_match",
        "plaintext_zip_sha256_match",
        "asset_count",
        "serve_private",
        "verification_status",
        "cleanup_status",
        "status_token",
    }
    for line in lines:
        tokens = line.split()
        if not tokens:
            continue
        normalized = []
        for token in tokens:
            if token.count("=") != 1:
                normalized = []
                break
            key, value = token.split("=", 1)
            if key not in allowed_keys or not re.fullmatch(r"[A-Za-z0-9._:+,@/#-]{1,180}", value):
                normalized = []
                break
            if (
                key != "cleanup_status"
                and any(marker.lower() in value.lower() for marker in ("secret", "token", "private_key", "password", "credential"))
            ):
                normalized = []
                break
            normalized.append(token)
        if normalized:
            safe.append(" ".join(normalized))
    return safe
