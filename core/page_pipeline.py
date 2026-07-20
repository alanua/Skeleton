from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import os
import re
import shutil
import stat
import tempfile
import time
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

import yaml

from core.page_publisher_backends import (
    BackendContext,
    BackendError,
    default_fetcher,
    default_runner,
    get_backend,
)
from core.page_publisher_profiles import PublicationProfile, get_profile
from core.page_renderer_registry import resolve_renderer

PIPELINE_ROOT_ENV = "SKELETON_PAGE_PIPELINE_ROOT"
DEFAULT_PIPELINE_ROOT = Path.home() / ".local" / "share" / "skeleton" / "page-pipeline"
PUBLISH_APPROVALS = {"publish_page_v1", "publish_page_adopt_v1"}
OWNER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
TEMPLATE_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,95}$")
PRIVATE_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "ghp_",
    "github_pat_",
)
TEXT_SUFFIXES = {".html", ".css", ".js", ".json", ".txt", ".md", ".svg", ".webmanifest"}

CommandRunner = Callable[[list[str], Path | None, int], tuple[int, str]]
UrlFetcher = Callable[[str, int], tuple[int, bytes]]
DownstreamAction = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]


@dataclass(frozen=True)
class BuildArtifact:
    rendered_dir: Path
    package_path: Path
    content_hash: str
    package_hash: str
    asset_count: int
    file_count: int
    total_bytes: int
    image_metadata_count: int


class _PageParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.refs: list[tuple[str, str]] = []
        self.external_images: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = True
        values = {str(key).lower(): value for key, value in attrs}
        for attr in ("src", "href"):
            value = values.get(attr)
            if value:
                self.refs.append((attr, value))
        if lower == "img" and values.get("src"):
            src = str(values["src"])
            if _is_external_ref(src):
                self.external_images.append((src, str(values.get("alt") or "")))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


_ACTIONS: dict[str, DownstreamAction] = {}


def register_downstream_action(action_id: str, action: DownstreamAction, *, replace: bool = False) -> None:
    if not action_id or not callable(action):
        raise ValueError("invalid_downstream_action")
    if action_id in _ACTIONS and not replace:
        raise ValueError("downstream_action_already_registered")
    _ACTIONS[action_id] = action


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.chmod(mode)
    temp.replace(path)
    path.chmod(mode)


def _pipeline_root(root: Path | None) -> Path:
    resolved = (root or Path(os.environ.get(PIPELINE_ROOT_ENV, DEFAULT_PIPELINE_ROOT))).expanduser().resolve(strict=False)
    resolved.mkdir(parents=True, exist_ok=True)
    try:
        resolved.chmod(0o700)
    except OSError:
        pass
    return resolved


def _load_serialized(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def _resolve_manifest_ref(base: Path, raw: Any) -> str:
    value = str(raw)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme or value.startswith("/"):
        return value
    return str((base / value).resolve(strict=False))


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve(strict=True)
    raw = _load_serialized(manifest_path)
    if not isinstance(raw, dict):
        raise ValueError("manifest_not_object")
    manifest = dict(raw)
    base = manifest_path.parent
    for field in ("content_ref", "asset_manifest_ref", "content_assets_ref"):
        if manifest.get(field):
            manifest[field] = _resolve_manifest_ref(base, manifest[field])
    options = manifest.get("backend_options")
    if isinstance(options, dict):
        options = dict(options)
        for field in ("root", "repository_path", "fragment_key_file"):
            if options.get(field):
                options[field] = _resolve_manifest_ref(base, options[field])
        manifest["backend_options"] = options
    manifest["_manifest_path"] = str(manifest_path)
    validate_manifest(manifest)
    return manifest


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "schemas" / "page_publication_manifest_v1.schema.json"


def _optional_jsonschema_validation(manifest: dict[str, Any]) -> None:
    try:
        import jsonschema
    except ImportError:
        return
    schema_path = _schema_path()
    if not schema_path.is_file():
        return
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    public_manifest = {key: value for key, value in manifest.items() if not key.startswith("_")}
    try:
        jsonschema.validate(public_manifest, schema)
    except jsonschema.ValidationError as exc:
        raise ValueError("manifest_schema_invalid") from exc


def validate_manifest(manifest: dict[str, Any]) -> PublicationProfile:
    required = {
        "schema_version", "owner_module", "publication_profile_id", "page_id",
        "template_id", "content_ref", "asset_manifest_ref", "publication_mode",
    }
    if missing := sorted(required - set(manifest)):
        raise ValueError(f"manifest_missing_{missing[0]}")
    if manifest["schema_version"] not in {1, "1", "1.0"}:
        raise ValueError("unsupported_manifest_version")
    owner = str(manifest["owner_module"])
    if OWNER_RE.fullmatch(owner) is None:
        raise ValueError("invalid_owner_module")
    profile = get_profile(str(manifest["publication_profile_id"]))
    if profile is None:
        raise ValueError("unknown_publication_profile")
    if profile.owner_project not in {"*", owner}:
        raise ValueError("profile_owner_mismatch")
    page_id = str(manifest["page_id"])
    if not profile.validate_page_id(page_id):
        raise ValueError("invalid_page_id")
    if TEMPLATE_RE.fullmatch(str(manifest["template_id"])) is None:
        raise ValueError("invalid_template_id")
    if manifest["publication_mode"] not in {"create", "update_owned"}:
        raise ValueError("invalid_publication_mode")
    if not isinstance(manifest.get("backend_options", {}), dict):
        raise ValueError("invalid_backend_options")
    actions = manifest.get("downstream_actions", [])
    if not isinstance(actions, list):
        raise ValueError("invalid_downstream_actions")
    if manifest.get("evidence_metadata") is not None and not isinstance(manifest["evidence_metadata"], dict):
        raise ValueError("invalid_evidence_metadata")
    for ref_field in ("content_ref", "asset_manifest_ref"):
        parsed = urllib.parse.urlparse(str(manifest[ref_field]))
        if parsed.scheme not in {"", "file"}:
            raise ValueError(f"unsupported_{ref_field}")
    _optional_jsonschema_validation(manifest)
    return profile


def _is_external_ref(ref: str) -> bool:
    parsed = urllib.parse.urlparse(ref)
    return bool(parsed.scheme in {"http", "https"} or parsed.netloc or ref.startswith("//") or ref.startswith("data:"))


def _load_asset_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError("asset_manifest_missing")
    raw = _load_serialized(path)
    if isinstance(raw, dict):
        raw = raw.get("assets", [])
    if not isinstance(raw, list):
        raise ValueError("asset_manifest_invalid")
    assets: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("asset_metadata_invalid")
        assets.append(dict(item))
    return assets


def _validate_asset_metadata(assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    for item in assets:
        required = {"subject", "source_url", "author", "license", "retrieval_date", "alt_text"}
        if missing := required - set(item):
            raise ValueError(f"asset_metadata_missing_{sorted(missing)[0]}")
        source_url = str(item["source_url"])
        if urllib.parse.urlparse(source_url).scheme != "https":
            raise ValueError("asset_source_not_https")
        try:
            date.fromisoformat(str(item["retrieval_date"]))
        except ValueError as exc:
            raise ValueError("asset_retrieval_date_invalid") from exc
        if not str(item["license"]).strip() or not str(item["alt_text"]).strip():
            raise ValueError("asset_metadata_empty")
        for key in ("asset_url", "source_url"):
            if item.get(key):
                by_url[str(item[key])] = item
    return by_url


def _safe_local_ref(site_dir: Path, ref: str) -> Path | None:
    parsed = urllib.parse.urlparse(ref)
    if parsed.scheme or parsed.netloc or ref.startswith("#") or ref.startswith("data:") or ref.startswith("mailto:") or ref.startswith("tel:"):
        return None
    if parsed.path.startswith("/") or not parsed.path:
        return None
    rel = PurePosixPath(parsed.path)
    if ".." in rel.parts:
        raise ValueError("asset_reference_traversal")
    path = site_dir.joinpath(*rel.parts)
    if not path.is_file():
        raise ValueError("referenced_asset_missing")
    return path


def _validate_rendered(site_dir: Path, profile: PublicationProfile, asset_manifest_path: Path) -> tuple[int, int, int, int]:
    index = site_dir / "index.html"
    if not index.is_file():
        raise ValueError("render_missing_index")
    if any(path.is_symlink() for path in site_dir.rglob("*")):
        raise ValueError("render_contains_symlink")
    assets = _load_asset_manifest(asset_manifest_path)
    by_url = _validate_asset_metadata(assets)
    count = 0
    total = 0
    local_ref_count = 0
    external_images: list[tuple[str, str]] = []
    for path in sorted(site_dir.rglob("*")):
        if not path.is_file():
            continue
        count += 1
        if count > profile.max_entries:
            raise ValueError("render_entry_limit_exceeded")
        suffix = path.suffix.lower()
        if suffix not in profile.allowed_extensions:
            raise ValueError("render_extension_not_allowed")
        size = path.stat().st_size
        total += size
        if total > profile.max_uncompressed_bytes:
            raise ValueError("render_size_limit_exceeded")
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="strict")
            if any(marker in text for marker in PRIVATE_MARKERS):
                raise ValueError("render_secret_marker")
            if suffix == ".html":
                parser = _PageParser()
                parser.feed(text)
                if path == index and not parser.title.strip():
                    raise ValueError("render_missing_title")
                for _, ref in parser.refs:
                    if _safe_local_ref(site_dir, ref) is not None:
                        local_ref_count += 1
                external_images.extend(parser.external_images)
    for src, alt in external_images:
        metadata = by_url.get(src)
        if metadata is None:
            raise ValueError("external_image_metadata_missing")
        if urllib.parse.urlparse(src).scheme != "https":
            raise ValueError("external_image_not_https")
        if not alt.strip():
            raise ValueError("external_image_alt_missing")
        if str(metadata["alt_text"]).strip() != alt.strip():
            raise ValueError("external_image_alt_mismatch")
    return count, total, local_ref_count, len(assets)


def _content_hash(site_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in site_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(site_dir).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _deterministic_zip(site_dir: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.tmp")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in site_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(site_dir).as_posix()
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes())
    package_hash = hashlib.sha256(temp.read_bytes()).hexdigest()
    temp.replace(destination)
    destination.chmod(0o600)
    return package_hash


def build_manifest(
    manifest_or_path: dict[str, Any] | str | Path,
    *,
    root: Path | None = None,
) -> tuple[dict[str, Any], BuildArtifact]:
    started = time.monotonic()
    manifest = load_manifest(manifest_or_path) if not isinstance(manifest_or_path, dict) else dict(manifest_or_path)
    profile = validate_manifest(manifest)
    pipeline_root = _pipeline_root(root)
    temp_root = pipeline_root / "tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    rendered = Path(tempfile.mkdtemp(prefix="render-", dir=temp_root))
    try:
        renderer = resolve_renderer(manifest)
        renderer(manifest, rendered)
        file_count, total_bytes, asset_count, image_metadata_count = _validate_rendered(
            rendered,
            profile,
            Path(str(manifest["asset_manifest_ref"])),
        )
        content_hash = _content_hash(rendered)
        package_path = pipeline_root / "builds" / f"{content_hash}.zip"
        package_hash = _deterministic_zip(rendered, package_path)
        artifact = BuildArtifact(
            rendered_dir=rendered,
            package_path=package_path,
            content_hash=content_hash,
            package_hash=package_hash,
            asset_count=asset_count,
            file_count=file_count,
            total_bytes=total_bytes,
            image_metadata_count=image_metadata_count,
        )
        receipt = {
            "schema": "skeleton.page.build_receipt.v1",
            "status": "BUILT",
            "owner_module": manifest["owner_module"],
            "profile_id": profile.profile_id,
            "page_id": manifest["page_id"],
            "content_hash": content_hash,
            "package_hash": package_hash,
            "package_path": str(package_path),
            "file_count": file_count,
            "asset_count": asset_count,
            "image_metadata_count": image_metadata_count,
            "total_bytes": total_bytes,
            "build_seconds": round(time.monotonic() - started, 4),
            "created_at": _utc_now(),
        }
        return receipt, artifact
    except Exception:
        shutil.rmtree(rendered, ignore_errors=True)
        raise


def _state_path(root: Path, manifest: dict[str, Any], profile: PublicationProfile) -> Path:
    return root / "state" / profile.profile_id / str(manifest["owner_module"]) / f"{manifest['page_id']}.json"


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("publication_state_invalid")
    return value if isinstance(value, dict) else None


def _run_actions(manifest: dict[str, Any], private_receipt: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for entry in manifest.get("downstream_actions", []):
        if isinstance(entry, str):
            action_id, config = entry, {}
        elif isinstance(entry, dict):
            action_id = str(entry.get("action_id") or "")
            config = dict(entry.get("config") or {})
        else:
            results.append({"status": "ERROR", "reason": "invalid_action_entry"})
            continue
        action = _ACTIONS.get(action_id)
        if action is None:
            results.append({"action_id": action_id, "status": "ERROR", "reason": "unknown_action"})
            continue
        try:
            result = action(private_receipt, config) or {}
            results.append({"action_id": action_id, "status": "DONE", **result})
        except Exception as exc:
            results.append({"action_id": action_id, "status": "ERROR", "reason": type(exc).__name__})
    return results


def publish_manifest(
    manifest_or_path: dict[str, Any] | str | Path,
    *,
    root: Path | None = None,
    command_runner: CommandRunner | None = None,
    url_fetcher: UrlFetcher | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    manifest = load_manifest(manifest_or_path) if not isinstance(manifest_or_path, dict) else dict(manifest_or_path)
    profile = validate_manifest(manifest)
    if manifest.get("operator_approval") not in PUBLISH_APPROVALS:
        return _blocked_receipt(manifest, profile, "missing_operator_approval")
    pipeline_root = _pipeline_root(root)
    state_path = _state_path(pipeline_root, manifest, profile)
    try:
        prior_state = _load_state(state_path)
    except ValueError as exc:
        return _blocked_receipt(manifest, profile, str(exc))
    if manifest["publication_mode"] == "create" and prior_state is not None:
        return _blocked_receipt(manifest, profile, "publication_state_already_exists")
    try:
        build_receipt, artifact = build_manifest(manifest, root=pipeline_root)
    except Exception as exc:
        return _blocked_receipt(manifest, profile, _safe_reason(exc), phase="build")
    try:
        if (
            prior_state
            and prior_state.get("content_hash") == artifact.content_hash
            and prior_state.get("verification") in {"LOCAL_VERIFIED", "HTTPS_VERIFIED"}
            and prior_state.get("stable_url")
        ):
            return {
                "schema": "skeleton.page.publication_receipt.v1",
                "status": "NO_CHANGE",
                "owner_module": manifest["owner_module"],
                "profile_id": profile.profile_id,
                "page_id": manifest["page_id"],
                "content_hash": artifact.content_hash,
                "package_hash": artifact.package_hash,
                "revision": prior_state.get("revision"),
                "stable_url": prior_state.get("stable_url"),
                "verification": prior_state.get("verification"),
                "rollback_ref": prior_state.get("rollback_ref"),
                "build_seconds": build_receipt["build_seconds"],
                "publish_seconds": 0.0,
                "created_at": _utc_now(),
            }
        backend = get_backend(profile.backend)
        if backend is None:
            return _blocked_receipt(manifest, profile, "publication_backend_not_registered")
        revision = artifact.content_hash
        backend_started = time.monotonic()
        context = BackendContext(
            manifest=manifest,
            profile=profile,
            rendered_dir=artifact.rendered_dir,
            package_hash=artifact.package_hash,
            content_hash=artifact.content_hash,
            revision=revision,
            pipeline_root=pipeline_root,
            prior_state=prior_state,
            command_runner=command_runner or default_runner(),
            url_fetcher=url_fetcher or default_fetcher(),
        )
        try:
            result = backend(context)
        except Exception as exc:
            return _blocked_receipt(manifest, profile, _safe_reason(exc), phase="publish")
        private_receipt = {
            "schema": "skeleton.page.private_publication_receipt.v1",
            "status": result.status,
            "owner_module": manifest["owner_module"],
            "profile_id": profile.profile_id,
            "page_id": manifest["page_id"],
            "content_hash": artifact.content_hash,
            "package_hash": artifact.package_hash,
            "revision": result.revision,
            "stable_url": result.stable_url,
            "private_url": result.private_url,
            "verification": result.verification,
            "rollback_ref": result.rollback_ref,
            "backend_metadata": result.backend_metadata,
            "build_seconds": build_receipt["build_seconds"],
            "publish_seconds": round(time.monotonic() - backend_started, 4),
            "total_seconds": round(time.monotonic() - started, 4),
            "created_at": _utc_now(),
        }
        state = {
            **private_receipt,
            "private": result.private_state,
            "manifest_path": manifest.get("_manifest_path"),
        }
        _atomic_json(state_path, state)
        receipt_path = pipeline_root / "receipts" / profile.profile_id / str(manifest["owner_module"]) / f"{manifest['page_id']}-{result.revision[:16]}.json"
        _atomic_json(receipt_path, private_receipt)
        actions: list[dict[str, Any]] = []
        if result.verification in {"LOCAL_VERIFIED", "HTTPS_VERIFIED"}:
            actions = _run_actions(manifest, private_receipt)
        public_receipt = {key: value for key, value in private_receipt.items() if key != "private_url"}
        public_receipt["private_receipt_path"] = str(receipt_path)
        public_receipt["downstream_actions"] = actions
        if any(item.get("status") == "ERROR" for item in actions):
            public_receipt["status"] = "PUBLISHED_WITH_ACTION_ERRORS"
        return public_receipt
    finally:
        shutil.rmtree(artifact.rendered_dir, ignore_errors=True)


def _safe_reason(exc: Exception) -> str:
    if isinstance(exc, BackendError):
        return exc.reason
    text = str(exc)
    if re.fullmatch(r"[a-z0-9_]{3,100}", text):
        return text
    return type(exc).__name__.lower()


def _blocked_receipt(
    manifest: dict[str, Any],
    profile: PublicationProfile,
    reason: str,
    *,
    phase: str = "validation",
) -> dict[str, Any]:
    safe = reason if re.fullmatch(r"[a-z0-9_]{3,100}", reason) else "publication_failed"
    return {
        "schema": "skeleton.page.publication_receipt.v1",
        "status": "BLOCKED",
        "owner_module": manifest.get("owner_module"),
        "profile_id": profile.profile_id,
        "page_id": manifest.get("page_id"),
        "phase": phase,
        "reason": safe,
        "created_at": _utc_now(),
    }


def _public_cli_receipt(receipt: dict[str, Any], show_private_url: bool) -> dict[str, Any]:
    result = dict(receipt)
    if not show_private_url:
        result.pop("private_url", None)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.page_pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "publish"):
        item = subparsers.add_parser(command)
        item.add_argument("--manifest", required=True)
        item.add_argument("--root")
        item.add_argument("--show-private-url", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root).expanduser() if args.root else None
    try:
        if args.command == "build":
            receipt, artifact = build_manifest(args.manifest, root=root)
            shutil.rmtree(artifact.rendered_dir, ignore_errors=True)
        else:
            receipt = publish_manifest(args.manifest, root=root)
    except Exception as exc:
        receipt = {"status": "BLOCKED", "phase": args.command, "reason": _safe_reason(exc), "created_at": _utc_now()}
    print(json.dumps(_public_cli_receipt(receipt, args.show_private_url), sort_keys=True, ensure_ascii=False))
    return 0 if receipt.get("status") in {"BUILT", "PUBLISHED", "NO_CHANGE", "PUBLISHED_WITH_ACTION_ERRORS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
