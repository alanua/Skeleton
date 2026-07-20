from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from core.page_publisher_profiles import PublicationProfile

CommandRunner = Callable[[list[str], Path | None, int], tuple[int, str]]
UrlFetcher = Callable[[str, int], tuple[int, bytes]]


class BackendError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class BackendContext:
    manifest: dict[str, Any]
    profile: PublicationProfile
    rendered_dir: Path
    package_hash: str
    content_hash: str
    revision: str
    pipeline_root: Path
    prior_state: dict[str, Any] | None
    command_runner: CommandRunner
    url_fetcher: UrlFetcher


@dataclass(frozen=True)
class BackendResult:
    status: str
    stable_url: str
    revision: str
    verification: str
    rollback_ref: str | None
    private_url: str | None = None
    private_state: dict[str, Any] = field(default_factory=dict)
    backend_metadata: dict[str, Any] = field(default_factory=dict)


Backend = Callable[[BackendContext], BackendResult]
_BACKENDS: dict[str, Backend] = {}
_OWNER_FILE = ".skeleton-page-owner.json"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico"}
_FONT_SUFFIXES = {".woff", ".woff2"}


def register_backend(backend_id: str, backend: Backend, *, replace: bool = False) -> None:
    if not backend_id or not callable(backend):
        raise ValueError("invalid_backend_registration")
    if backend_id in _BACKENDS and not replace:
        raise ValueError("backend_already_registered")
    _BACKENDS[backend_id] = backend


def get_backend(backend_id: str) -> Backend | None:
    return _BACKENDS.get(backend_id)


def _default_runner(args: list[str], cwd: Path | None, timeout: int) -> tuple[int, str]:
    import subprocess

    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout + result.stderr


def _default_fetcher(url: str, timeout: int) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "Skeleton-Page-Publisher/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status), response.read(512 * 1024)


def default_runner() -> CommandRunner:
    return _default_runner


def default_fetcher() -> UrlFetcher:
    return _default_fetcher


def _backend_options(manifest: dict[str, Any]) -> dict[str, Any]:
    options = manifest.get("backend_options") or {}
    if not isinstance(options, dict):
        raise BackendError("invalid_backend_options")
    return options


def _owner_payload(ctx: BackendContext) -> dict[str, Any]:
    return {
        "schema": "skeleton.page.route_owner.v1",
        "owner_module": ctx.manifest["owner_module"],
        "page_id": ctx.manifest["page_id"],
        "profile_id": ctx.profile.profile_id,
        "revision": ctx.revision,
        "content_hash": ctx.content_hash,
    }


def _read_owner(target: Path) -> dict[str, Any] | None:
    path = target / _OWNER_FILE
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _owned_by(ctx: BackendContext, owner: dict[str, Any] | None) -> bool:
    return bool(
        owner
        and owner.get("owner_module") == ctx.manifest["owner_module"]
        and owner.get("page_id") == ctx.manifest["page_id"]
        and owner.get("profile_id") == ctx.profile.profile_id
    )


def _check_route_policy(ctx: BackendContext, target: Path) -> None:
    mode = ctx.manifest["publication_mode"]
    exists = target.exists()
    owner = _read_owner(target) if exists else None
    options = _backend_options(ctx.manifest)
    adopt = bool(options.get("adopt_existing"))
    approval = ctx.manifest.get("operator_approval")
    if mode == "create" and exists:
        raise BackendError("publication_path_already_exists")
    if mode == "update_owned":
        if not exists:
            if ctx.prior_state is None:
                raise BackendError("owned_route_missing")
            return
        if _owned_by(ctx, owner):
            return
        if adopt and approval == "publish_page_adopt_v1":
            return
        raise BackendError("publication_path_not_owned")


def _copy_rendered(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, symlinks=False)
    for path in destination.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def _install_directory(staging: Path, target: Path, rollback_root: Path, revision: str) -> Path | None:
    target.parent.mkdir(parents=True, exist_ok=True)
    rollback: Path | None = None
    if target.exists():
        rollback_root.mkdir(parents=True, exist_ok=True)
        rollback = rollback_root / f"{revision}-{uuid.uuid4().hex[:10]}"
        target.rename(rollback)
    staging.rename(target)
    return rollback


def _restore_directory(target: Path, rollback: Path | None) -> None:
    if target.exists():
        shutil.rmtree(target)
    if rollback is not None and rollback.exists():
        rollback.rename(target)


def _route_target(root: Path, profile: PublicationProfile, page_id: str) -> Path:
    relative = profile.url_path(page_id).lstrip("/")
    parts = PurePosixPath(relative).parts
    if not parts or ".." in parts:
        raise BackendError("invalid_publication_path")
    return root.joinpath(*parts)


def _resolve_backend_root(ctx: BackendContext) -> Path:
    options = _backend_options(ctx.manifest)
    raw = options.get("repository_path") or options.get("root")
    if not raw and ctx.profile.publication_root_env:
        raw = os.environ.get(ctx.profile.publication_root_env)
    if not raw:
        raise BackendError("publication_root_not_configured")
    root = Path(str(raw)).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _join_url(base: str, path: str) -> str:
    if not base:
        raise BackendError("public_base_url_not_configured")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BackendError("public_base_url_not_https")
    return f"{base.rstrip('/')}/{path.strip('/')}/"


def _stable_url(ctx: BackendContext, base_url: str) -> str:
    route = ctx.profile.url_path(ctx.manifest["page_id"])
    declared = str(ctx.manifest.get("stable_url") or "").strip()
    if declared:
        parsed = urllib.parse.urlparse(declared)
        if parsed.scheme != "https" or not parsed.netloc:
            raise BackendError("stable_url_not_https")
        normalized = declared.rstrip("/") + "/"
        if not parsed.path.rstrip("/").endswith(route.rstrip("/")):
            raise BackendError("stable_url_path_mismatch")
        return normalized
    return _join_url(base_url, route)


def _filesystem_backend(ctx: BackendContext) -> BackendResult:
    root = _resolve_backend_root(ctx)
    target = _route_target(root, ctx.profile, ctx.manifest["page_id"])
    _check_route_policy(ctx, target)
    temp = target.parent / f".{target.name}.next-{uuid.uuid4().hex}"
    _copy_rendered(ctx.rendered_dir, temp)
    (temp / _OWNER_FILE).write_text(json.dumps(_owner_payload(ctx), sort_keys=True), encoding="utf-8")
    rollback_root = ctx.pipeline_root / "rollbacks" / ctx.profile.profile_id / ctx.manifest["owner_module"] / ctx.manifest["page_id"]
    rollback = _install_directory(temp, target, rollback_root, ctx.revision)
    try:
        if not (target / "index.html").is_file():
            raise BackendError("filesystem_verification_failed")
        options = _backend_options(ctx.manifest)
        base_url = str(options.get("base_url") or "")
        stable_url = _stable_url(ctx, base_url) if base_url or ctx.manifest.get("stable_url") else target.as_uri()
        return BackendResult(
            status="PUBLISHED",
            stable_url=stable_url,
            revision=ctx.revision,
            verification="LOCAL_VERIFIED",
            rollback_ref=str(rollback) if rollback else None,
            backend_metadata={"target": str(target)},
        )
    except Exception:
        _restore_directory(target, rollback)
        raise


def _is_external(ref: str) -> bool:
    parsed = urllib.parse.urlparse(ref)
    return bool(parsed.scheme or parsed.netloc or ref.startswith("//") or ref.startswith("#") or ref.startswith("data:"))


def _safe_local_path(root: Path, ref: str, *, relative_to: Path | None = None) -> Path:
    parsed = urllib.parse.urlparse(ref)
    rel = PurePosixPath(parsed.path)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise BackendError("bundle_unsafe_asset_path")
    base = relative_to if relative_to is not None else root
    path = base.joinpath(*rel.parts).resolve(strict=False)
    try:
        path.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise BackendError("bundle_asset_escape") from exc
    if not path.is_file():
        raise BackendError("bundle_missing_asset")
    return path


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _inline_css_urls(css: str, root: Path, css_dir: Path) -> str:
    pattern = re.compile(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", re.IGNORECASE)

    def replace(match: re.Match[str]) -> str:
        ref = match.group(2).strip()
        if _is_external(ref):
            return match.group(0)
        path = _safe_local_path(root, ref, relative_to=css_dir)
        if path.suffix.lower() not in (_IMAGE_SUFFIXES | _FONT_SUFFIXES):
            raise BackendError("bundle_unsupported_css_asset")
        return f'url("{_data_uri(path)}")'

    return pattern.sub(replace, css)


def _bundle_single_html(root: Path) -> bytes:
    index = root / "index.html"
    if not index.is_file():
        raise BackendError("bundle_missing_index")
    html = index.read_text(encoding="utf-8")
    link_pattern = re.compile(r"<link\b[^>]*>", re.IGNORECASE)

    def replace_link(match: re.Match[str]) -> str:
        tag = match.group(0)
        href_match = re.search(r"\bhref\s*=\s*(['\"])(.*?)\1", tag, re.IGNORECASE)
        rel_match = re.search(r"\brel\s*=\s*(['\"])(.*?)\1", tag, re.IGNORECASE)
        if not href_match or not rel_match or "stylesheet" not in rel_match.group(2).lower().split():
            return tag
        ref = href_match.group(2)
        if _is_external(ref):
            return tag
        path = _safe_local_path(root, ref)
        if path.suffix.lower() != ".css":
            return tag
        css = _inline_css_urls(path.read_text(encoding="utf-8"), root, path.parent)
        css = css.replace("</style", "<\\/style")
        return f'<style data-skeleton-bundled="stylesheet">{css}</style>'

    html = link_pattern.sub(replace_link, html)
    script_pattern = re.compile(r"<script\b(?P<attrs>[^>]*)\bsrc\s*=\s*(['\"])(?P<src>.*?)\2(?P<rest>[^>]*)>\s*</script>", re.IGNORECASE | re.DOTALL)

    def replace_script(match: re.Match[str]) -> str:
        ref = match.group("src")
        if _is_external(ref):
            return match.group(0)
        path = _safe_local_path(root, ref)
        if path.suffix.lower() != ".js":
            return match.group(0)
        script = path.read_text(encoding="utf-8").replace("</script", "<\\/script")
        return f'<script data-skeleton-bundled="script">{script}</script>'

    html = script_pattern.sub(replace_script, html)
    src_pattern = re.compile(r"(?P<prefix>\bsrc\s*=\s*)(?P<quote>['\"])(?P<src>.*?)(?P=quote)", re.IGNORECASE)

    def replace_src(match: re.Match[str]) -> str:
        ref = match.group("src")
        if _is_external(ref):
            return match.group(0)
        path = _safe_local_path(root, ref)
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            raise BackendError("bundle_unsupported_src_asset")
        quote = match.group("quote")
        return f"{match.group('prefix')}{quote}{_data_uri(path)}{quote}"

    html = src_pattern.sub(replace_src, html)
    unresolved = []
    for attr, ref in re.findall(r"\b(src|href)\s*=\s*['\"]([^'\"]+)['\"]", html, re.IGNORECASE):
        if not _is_external(ref) and not (attr.lower() == "href" and ref.startswith("?")):
            unresolved.append(ref)
    if unresolved:
        raise BackendError("bundle_unresolved_local_asset")
    return html.encode("utf-8")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


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


def _encrypt_html(ctx: BackendContext, plaintext: bytes, secret: bytes) -> dict[str, str | int]:
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(16)
    material = _hkdf(secret, salt, b"skeleton-page-publisher-v1", 64)
    enc_key, mac_key = material[:32], material[32:]
    tmp_root = ctx.pipeline_root / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="encrypt-", dir=tmp_root))
    plain_path = work / "page.html"
    cipher_path = work / "page.bin"
    try:
        plain_path.write_bytes(plaintext)
        code, _ = ctx.command_runner(
            [
                "openssl", "enc", "-aes-256-ctr", "-K", enc_key.hex(), "-iv", iv.hex(),
                "-in", str(plain_path), "-out", str(cipher_path),
            ],
            None,
            30,
        )
        if code != 0 or not cipher_path.is_file():
            raise BackendError("encryption_failed")
        ciphertext = cipher_path.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    authenticated = b"SPP1" + salt + iv + ciphertext
    tag = hmac.new(mac_key, authenticated, hashlib.sha256).digest()
    return {
        "version": 1,
        "revision": ctx.revision,
        "salt": _b64url(salt),
        "iv": _b64url(iv),
        "ciphertext": _b64url(ciphertext),
        "mac": _b64url(tag),
    }


def _loader_html(revision: str) -> str:
    safe_revision = re.sub(r"[^a-f0-9]", "", revision)[:64]
    return f'''<!doctype html>
<html lang="uk" data-skeleton-page-revision="{safe_revision}">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow,noarchive,nosnippet,noimageindex"><meta name="referrer" content="no-referrer">
<title>Приватна сторінка</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;font:16px system-ui;background:#f6f8f7;color:#17201d}}main{{width:min(88vw,520px);background:#fff;border:1px solid #dfe8e4;border-radius:16px;padding:28px;box-shadow:0 16px 50px #10241c14}}.bar{{height:7px;background:#dfece7;border-radius:99px;overflow:hidden;margin-top:22px}}.bar:after{{content:"";display:block;width:38%;height:100%;background:#0b6e5b;animation:m 1.1s infinite alternate}}@keyframes m{{to{{transform:translateX(165%)}}}}.error{{color:#8b2d1f}}</style>
</head><body><main aria-live="polite"><h1>Приватна сторінка</h1><p id="status">Перевіряю посилання…</p><div class="bar" id="bar"></div></main>
<script>(()=>{{'use strict';const rev='{safe_revision}',te=new TextEncoder(),td=new TextDecoder();const b64=s=>{{s=s.replace(/-/g,'+').replace(/_/g,'/');s+='='.repeat((4-s.length%4)%4);return Uint8Array.from(atob(s),c=>c.charCodeAt(0))}};const cat=(...a)=>{{const n=a.reduce((s,x)=>s+x.length,0),o=new Uint8Array(n);let p=0;for(const x of a){{o.set(x,p);p+=x.length}}return o}};const fail=()=>{{document.getElementById('status').textContent='Посилання недійсне або сторінка пошкоджена.';document.getElementById('status').className='error';document.getElementById('bar').remove()}};(async()=>{{try{{const params=new URLSearchParams(location.hash.slice(1)),keyText=params.get('k');if(!keyText)return fail();const secret=b64(keyText);if(secret.length!==32)return fail();const payload=await fetch('payload.json?rev='+rev,{{cache:'no-store',referrerPolicy:'no-referrer'}}).then(r=>{{if(!r.ok)throw 0;return r.json()}});if(payload.version!==1||payload.revision!==rev)throw 0;const base=await crypto.subtle.importKey('raw',secret,'HKDF',false,['deriveBits']);const bits=await crypto.subtle.deriveBits({{name:'HKDF',hash:'SHA-256',salt:b64(payload.salt),info:te.encode('skeleton-page-publisher-v1')}},base,512);const material=new Uint8Array(bits),enc=material.slice(0,32),mac=material.slice(32),macKey=await crypto.subtle.importKey('raw',mac,{{name:'HMAC',hash:'SHA-256'}},false,['verify']);const cipher=b64(payload.ciphertext),auth=cat(te.encode('SPP1'),b64(payload.salt),b64(payload.iv),cipher);if(!await crypto.subtle.verify('HMAC',macKey,b64(payload.mac),auth))throw 0;const aes=await crypto.subtle.importKey('raw',enc,'AES-CTR',false,['decrypt']);const plain=await crypto.subtle.decrypt({{name:'AES-CTR',counter:b64(payload.iv),length:128}},aes,cipher);const page=td.decode(plain);if(!/^<!doctype html>/i.test(page))throw 0;params.delete('k');history.replaceState(null,'',params.size?'#'+params.toString():location.pathname+location.search);document.open();document.write(page);document.close()}}catch{{fail()}}}})()}})();</script></body></html>'''


def _verify_local_encrypted(target: Path, revision: str) -> None:
    loader = target / "index.html"
    payload = target / "payload.json"
    if not loader.is_file() or not payload.is_file():
        raise BackendError("encrypted_output_missing")
    if revision not in loader.read_text(encoding="utf-8"):
        raise BackendError("loader_revision_mismatch")
    try:
        data = json.loads(payload.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackendError("payload_invalid_json") from exc
    required = {"version", "revision", "salt", "iv", "ciphertext", "mac"}
    if not required.issubset(data) or data.get("revision") != revision:
        raise BackendError("payload_invalid")


def _verify_https(ctx: BackendContext, stable_url: str) -> None:
    options = _backend_options(ctx.manifest)
    attempts = int(options.get("verify_attempts", 6))
    delay = float(options.get("verify_delay_seconds", 2.0))
    timeout = int(options.get("verify_timeout_seconds", 15))
    marker = f'data-skeleton-page-revision="{ctx.revision}"'.encode("ascii")
    last_error = "https_verification_failed"
    for attempt in range(max(1, attempts)):
        try:
            status, body = ctx.url_fetcher(stable_url, timeout)
            if status == 200 and marker in body:
                payload_status, payload_body = ctx.url_fetcher(f"{stable_url.rstrip('/')}/payload.json?rev={ctx.revision}", timeout)
                if payload_status == 200 and ctx.revision.encode("ascii") in payload_body:
                    return
        except Exception:
            last_error = "https_verification_unavailable"
        if attempt + 1 < attempts and delay > 0:
            time.sleep(delay)
    raise BackendError(last_error)


def _git_publish(ctx: BackendContext, repo_root: Path, target: Path) -> str | None:
    options = _backend_options(ctx.manifest)
    mode = str(options.get("git_mode", "none"))
    if mode not in {"none", "commit", "commit_push"}:
        raise BackendError("invalid_git_mode")
    if mode == "none":
        return None
    relative = target.relative_to(repo_root).as_posix()
    commands = [
        ["git", "add", "--", relative],
        ["git", "commit", "-m", f"Publish {ctx.manifest['owner_module']} page {ctx.manifest['page_id']} ({ctx.revision[:12]})"],
    ]
    if mode == "commit_push":
        commands.append(["git", "push"])
    for command in commands:
        code, _ = ctx.command_runner(command, repo_root, 90)
        if code != 0:
            raise BackendError("git_publication_failed")
    code, output = ctx.command_runner(["git", "rev-parse", "HEAD"], repo_root, 15)
    if code != 0:
        raise BackendError("git_revision_unavailable")
    sha = output.strip().splitlines()[0] if output.strip() else ""
    return sha if re.fullmatch(r"[0-9a-f]{40,64}", sha) else None


def _git_rollback(ctx: BackendContext, repo_root: Path, target: Path) -> None:
    options = _backend_options(ctx.manifest)
    mode = str(options.get("git_mode", "none"))
    if mode == "none":
        return
    relative = target.relative_to(repo_root).as_posix()
    for command in (["git", "add", "--", relative], ["git", "commit", "-m", f"Rollback page {ctx.manifest['page_id']} after verification failure"]):
        code, _ = ctx.command_runner(command, repo_root, 90)
        if code != 0:
            return
    if mode == "commit_push":
        ctx.command_runner(["git", "push"], repo_root, 90)


def _github_pages_encrypted_backend(ctx: BackendContext) -> BackendResult:
    repo_root = _resolve_backend_root(ctx)
    target = _route_target(repo_root, ctx.profile, ctx.manifest["page_id"])
    _check_route_policy(ctx, target)
    plaintext = _bundle_single_html(ctx.rendered_dir)
    prior_private = (ctx.prior_state or {}).get("private") or {}
    key_text = prior_private.get("fragment_key") if isinstance(prior_private, dict) else None
    options = _backend_options(ctx.manifest)
    if not key_text and options.get("fragment_key_file"):
        key_file = Path(str(options["fragment_key_file"])).expanduser().resolve(strict=True)
        if key_file.stat().st_mode & 0o077:
            raise BackendError("fragment_key_file_permissions")
        key_text = key_file.read_text(encoding="ascii").strip()
    if not key_text and options.get("fragment_key_env"):
        key_text = os.environ.get(str(options["fragment_key_env"]))
    if key_text:
        try:
            padded = str(key_text) + "=" * ((4 - len(str(key_text)) % 4) % 4)
            secret = base64.urlsafe_b64decode(padded)
        except Exception as exc:
            raise BackendError("stored_fragment_key_invalid") from exc
        if len(secret) != 32:
            raise BackendError("stored_fragment_key_invalid")
    else:
        secret = secrets.token_bytes(32)
        key_text = _b64url(secret)
    payload = _encrypt_html(ctx, plaintext, secret)
    temp = target.parent / f".{target.name}.next-{uuid.uuid4().hex}"
    temp.mkdir(parents=True, exist_ok=False)
    (temp / "index.html").write_text(_loader_html(ctx.revision), encoding="utf-8")
    (temp / "payload.json").write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="ascii")
    (temp / _OWNER_FILE).write_text(json.dumps(_owner_payload(ctx), sort_keys=True), encoding="utf-8")
    for path in temp.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    rollback_root = ctx.pipeline_root / "rollbacks" / ctx.profile.profile_id / ctx.manifest["owner_module"] / ctx.manifest["page_id"]
    rollback = _install_directory(temp, target, rollback_root, ctx.revision)
    commit_sha: str | None = None
    try:
        _verify_local_encrypted(target, ctx.revision)
        commit_sha = _git_publish(ctx, repo_root, target)
        base_url = str(options.get("base_url") or (os.environ.get(ctx.profile.public_base_url_env or "") if ctx.profile.public_base_url_env else ""))
        stable_url = _stable_url(ctx, base_url) if base_url or ctx.manifest.get("stable_url") else target.as_uri()
        verification_mode = str(options.get("verification_mode", "https" if base_url and options.get("git_mode") == "commit_push" else "local"))
        if verification_mode == "https":
            _verify_https(ctx, stable_url)
            verification = "HTTPS_VERIFIED"
        elif verification_mode == "local":
            verification = "LOCAL_VERIFIED"
        else:
            raise BackendError("invalid_verification_mode")
        private_url = f"{stable_url}#k={key_text}"
        return BackendResult(
            status="PUBLISHED",
            stable_url=stable_url,
            private_url=private_url,
            revision=ctx.revision,
            verification=verification,
            rollback_ref=str(rollback) if rollback else None,
            private_state={"fragment_key": key_text},
            backend_metadata={"target": str(target), "commit_sha": commit_sha},
        )
    except Exception:
        _restore_directory(target, rollback)
        try:
            _git_rollback(ctx, repo_root, target)
        except Exception:
            pass
        raise


def _tailscale_backend(ctx: BackendContext) -> BackendResult:
    try:
        from core import page_publisher_runtime as legacy
    except ImportError as exc:
        raise BackendError("tailscale_runtime_unavailable") from exc
    mode = ctx.manifest["publication_mode"]
    url_path = ctx.profile.url_path(ctx.manifest["page_id"])
    status, reason = legacy._tailscale_serve_status(ctx.command_runner)
    if reason is not None or status is None:
        raise BackendError("publication_backend_state_unavailable")
    configured, bounded = legacy._tailscale_path_configured(status, url_path)
    if not bounded:
        raise BackendError("publication_backend_state_unavailable")
    if mode == "create" and configured:
        raise BackendError("publication_path_already_exists")
    if mode == "update_owned" and not configured:
        raise BackendError("owned_route_missing")
    if mode == "update_owned" and ctx.prior_state is None:
        raise BackendError("publication_path_not_owned")
    current = ctx.pipeline_root / "sites" / ctx.profile.profile_id / ctx.manifest["owner_module"] / ctx.manifest["page_id"] / "current"
    publish_dir, previous = legacy._publish_site(ctx.rendered_dir, current)
    route_created = False
    try:
        if not configured:
            code, _ = legacy._set_tailscale_path(ctx.command_runner, url_path, publish_dir)
            if code != 0:
                raise BackendError("publication_backend_set_failed")
            route_created = True
        dns = legacy._tailscale_magic_dns_name(ctx.command_runner)
        if dns is None:
            raise BackendError("publication_backend_identity_unavailable")
        stable_url = f"https://{dns}{url_path}"
        ok, asset_count = legacy._verify_deployed_site(stable_url, publish_dir, ctx.url_fetcher)
        if not ok:
            raise BackendError("https_verification_failed")
        return BackendResult(
            status="PUBLISHED",
            stable_url=stable_url,
            private_url=stable_url,
            revision=ctx.revision,
            verification="HTTPS_VERIFIED",
            rollback_ref=str(previous) if previous else None,
            backend_metadata={"asset_count": asset_count, "target": str(publish_dir)},
        )
    except Exception:
        if route_created:
            legacy._remove_tailscale_path(ctx.command_runner, url_path)
        legacy._restore_published_site(current, previous)
        raise


register_backend("filesystem_static_v1", _filesystem_backend)
register_backend("github_pages_encrypted_v1", _github_pages_encrypted_backend)
register_backend("tailscale_serve_static_v1", _tailscale_backend)
