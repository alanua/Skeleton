from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


ROOT = Path(__file__).resolve().parents[2]

JOB_SCHEMA = "skeleton.home_edge.visual_capture.job.v1"
MANIFEST_SCHEMA = "skeleton.home_edge.visual_capture.manifest.v1"
RECEIPT_SCHEMA = "skeleton.home_edge.visual_capture.receipt.v1"

VISUAL_CAPTURE_SPOOL_ENV = "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_SPOOL"
VISUAL_CAPTURE_ARTIFACT_ROOT_ENV = "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_ARTIFACT_ROOT"
VISUAL_CAPTURE_BROWSER_PROFILE_ENV = "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_BROWSER_PROFILE"
VISUAL_CAPTURE_VISIBLE_KIOSK_ENV = "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_VISIBLE_KIOSK"
VISUAL_CAPTURE_YTDLP_ENV = "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_YTDLP"
VISUAL_CAPTURE_FFMPEG_ENV = "SKELETON_HOME_EDGE_01_VISUAL_CAPTURE_FFMPEG"

DEFAULT_OFFSETS_SECONDS = (-3, -1, 0, 1, 3)
MAX_FRAMES = 7
MIN_OFFSET_SECONDS = -10
MAX_OFFSET_SECONDS = 10
MAX_DRIFT_SECONDS = 1.5
ALLOWED_CAPTURE_MODES = frozenset({"background", "visible_kiosk"})
TERMINAL_STATUSES = frozenset(
    {
        "CAPTURED",
        "HUMAN_REVIEW_PENDING",
        "VERIFIED",
        "NEEDS_RECAPTURE",
        "NOT_VISIBLE",
        "INTERACTION_REQUIRED",
        "FAILED_RETRYABLE",
        "FAILED_TERMINAL",
    }
)
FORBIDDEN_JOB_FIELDS = frozenset(
    {
        "command",
        "commands",
        "selector",
        "selectors",
        "executable",
        "executable_path",
        "output",
        "output_path",
        "artifact_root",
        "profile",
        "profile_path",
        "host",
        "hosts",
        "user",
        "username",
        "port",
        "ports",
        "stdout",
        "stderr",
    }
)
INTERACTION_REASON_CODES = frozenset(
    {
        "consent_required",
        "login_required",
        "age_check_required",
        "cookie_prompt_required",
        "interaction_required",
    }
)
PRIVATE_MANIFEST_NAME = "manifest.private.json"
PUBLIC_RECEIPT_NAME = "receipt.public.json"
CONTACT_SHEET_NAME = "contact-sheet.private.bin"
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class VisualCaptureError(ValueError):
    """Raised when a visual capture job or private runtime path is unsafe."""


@dataclass(frozen=True)
class VisualCaptureRuntimeConfig:
    spool_root: Path
    artifact_root: Path
    browser_profile: Path
    visible_kiosk_enabled: bool = False
    yt_dlp_path: Path | None = None
    ffmpeg_path: Path | None = None


@dataclass(frozen=True)
class CapturedFrame:
    offset_seconds: int
    requested_time_seconds: float
    observed_time_seconds: float
    width: int
    height: int
    image_bytes: bytes


@dataclass(frozen=True)
class CaptureAdapterResult:
    status: str
    frames: tuple[CapturedFrame, ...] = ()
    reason_codes: tuple[str, ...] = ()
    retryable: bool = False
    human_review_required: bool = False
    temporary_paths: tuple[Path, ...] = ()


class VisualCaptureAdapter(Protocol):
    def capture(
        self,
        job: dict[str, Any],
        *,
        normalized_url: str,
        config: VisualCaptureRuntimeConfig,
        output_dir: Path,
    ) -> CaptureAdapterResult:
        ...


class BrowserFirstVisualCaptureAdapter:
    """Runtime placeholder for the private browser/media implementation."""

    def capture(
        self,
        job: dict[str, Any],
        *,
        normalized_url: str,
        config: VisualCaptureRuntimeConfig,
        output_dir: Path,
    ) -> CaptureAdapterResult:
        if not config.browser_profile.exists():
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("private_runtime_missing",),
                retryable=True,
            )
        return CaptureAdapterResult(
            status="FAILED_RETRYABLE",
            reason_codes=("private_browser_adapter_not_available",),
            retryable=True,
        )


def runtime_config_from_env(env: dict[str, str] | None = None) -> VisualCaptureRuntimeConfig:
    source = env if env is not None else os.environ
    spool = _required_env_path(source, VISUAL_CAPTURE_SPOOL_ENV)
    artifact_root = _required_env_path(source, VISUAL_CAPTURE_ARTIFACT_ROOT_ENV)
    profile = _required_env_path(source, VISUAL_CAPTURE_BROWSER_PROFILE_ENV)
    _validate_private_root(spool, "visual capture spool root")
    _validate_private_root(artifact_root, "visual capture artifact root")
    _validate_private_root(profile, "visual capture browser profile")
    return VisualCaptureRuntimeConfig(
        spool_root=spool,
        artifact_root=artifact_root,
        browser_profile=profile,
        visible_kiosk_enabled=source.get(VISUAL_CAPTURE_VISIBLE_KIOSK_ENV, "").strip()
        == "1",
        yt_dlp_path=_optional_fixed_executable(source, VISUAL_CAPTURE_YTDLP_ENV),
        ffmpeg_path=_optional_fixed_executable(source, VISUAL_CAPTURE_FFMPEG_ENV),
    )


def process_one_visual_capture_job(
    *,
    config: VisualCaptureRuntimeConfig | None = None,
    adapter: VisualCaptureAdapter | None = None,
) -> dict[str, Any]:
    active_config = config or runtime_config_from_env()
    _validate_private_root(active_config.spool_root, "visual capture spool root")
    _validate_private_root(active_config.artifact_root, "visual capture artifact root")
    _validate_private_root(active_config.browser_profile, "visual capture browser profile")
    job_path = _claim_one_job(active_config.spool_root)
    if job_path is None:
        return _empty_receipt()
    try:
        job = _read_job(job_path)
        receipt = run_visual_capture_job(
            job,
            config=active_config,
            adapter=adapter or BrowserFirstVisualCaptureAdapter(),
        )
        _move_job(job_path, active_config.spool_root / "done" / job_path.name)
        return receipt
    except Exception:
        _move_job(job_path, active_config.spool_root / "failed" / job_path.name)
        raise


def run_visual_capture_job(
    job: dict[str, Any],
    *,
    config: VisualCaptureRuntimeConfig,
    adapter: VisualCaptureAdapter,
) -> dict[str, Any]:
    normalized = validate_visual_capture_job(job, config=config)
    capture_dir = _capture_dir(config.artifact_root, normalized)
    receipt_path = capture_dir / PUBLIC_RECEIPT_NAME
    if receipt_path.exists():
        return _read_public_receipt(receipt_path)

    _prepare_private_directory(capture_dir)
    result = adapter.capture(
        normalized,
        normalized_url=normalized["normalized_url"],
        config=config,
        output_dir=capture_dir,
    )
    try:
        manifest = _build_manifest(normalized, result, capture_dir)
        manifest_path = capture_dir / PRIVATE_MANIFEST_NAME
        _write_private_json_atomic(manifest_path, manifest)
        manifest_hash = _sha256_bytes(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        receipt = _receipt_from_manifest(manifest, manifest_hash)
        _write_private_json_atomic(receipt_path, receipt)
        return receipt
    finally:
        _delete_temporary_paths(result.temporary_paths, capture_dir)


def validate_visual_capture_job(
    job: dict[str, Any], *, config: VisualCaptureRuntimeConfig
) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise VisualCaptureError("visual capture job must be an object")
    forbidden = sorted(set(job) & FORBIDDEN_JOB_FIELDS)
    if forbidden:
        raise VisualCaptureError(f"job contains issue-controlled private fields: {forbidden[0]}")
    required = {"schema", "action_id", "task_ref", "provider", "url", "requested_time_seconds"}
    if set(job) - {
        *required,
        "offsets_seconds",
        "capture_mode",
        "human_review_required",
    }:
        raise VisualCaptureError("visual capture job contains unknown fields")
    if job.get("schema") != JOB_SCHEMA:
        raise VisualCaptureError("unsupported visual capture job schema")
    if job.get("provider") != "youtube":
        raise VisualCaptureError("visual capture provider is not allowlisted")
    action_id = _safe_token(job.get("action_id"), "action_id")
    task_ref = _safe_token(job.get("task_ref"), "task_ref")
    requested_time = _validate_requested_time(job.get("requested_time_seconds"))
    offsets = _validate_offsets(job.get("offsets_seconds", list(DEFAULT_OFFSETS_SECONDS)))
    capture_mode = job.get("capture_mode", "background")
    if capture_mode not in ALLOWED_CAPTURE_MODES:
        raise VisualCaptureError("capture_mode is invalid")
    if capture_mode == "visible_kiosk" and not config.visible_kiosk_enabled:
        raise VisualCaptureError("visible_kiosk requires explicit private runtime selection")
    normalized_url = _normalize_youtube_watch_url(job.get("url"))
    return {
        "schema": JOB_SCHEMA,
        "action_id": action_id,
        "task_ref": task_ref,
        "provider": "youtube",
        "normalized_url": normalized_url,
        "requested_time_seconds": requested_time,
        "offsets_seconds": offsets,
        "capture_mode": capture_mode,
        "human_review_required": bool(job.get("human_review_required", False)),
    }


def _normalize_youtube_watch_url(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise VisualCaptureError("url is required")
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"www.youtube.com", "youtube.com"}:
        raise VisualCaptureError("only normal YouTube watch URLs are allowlisted")
    if parsed.path != "/watch":
        raise VisualCaptureError("only normal YouTube watch URLs are allowlisted")
    query = parse_qs(parsed.query, keep_blank_values=False)
    video_ids = query.get("v")
    if (
        not isinstance(video_ids, list)
        or len(video_ids) != 1
        or re.fullmatch(r"[A-Za-z0-9_-]{11}", video_ids[0] or "") is None
    ):
        raise VisualCaptureError("YouTube watch URL must contain one valid video id")
    clean_query = urlencode({"v": video_ids[0]})
    return urlunparse(("https", "www.youtube.com", "/watch", "", clean_query, ""))


def _validate_offsets(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise VisualCaptureError("offsets_seconds must be a non-empty list")
    if len(value) > MAX_FRAMES:
        raise VisualCaptureError("offsets_seconds may contain at most seven frames")
    offsets: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise VisualCaptureError("offsets_seconds entries must be integers")
        if item < MIN_OFFSET_SECONDS or item > MAX_OFFSET_SECONDS:
            raise VisualCaptureError("offsets_seconds entries must be within -10..10")
        offsets.append(item)
    return tuple(offsets)


def _validate_requested_time(value: Any) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise VisualCaptureError("requested_time_seconds must be numeric")
    if value < 0 or value > 86400:
        raise VisualCaptureError("requested_time_seconds is out of range")
    return float(value)


def _build_manifest(
    job: dict[str, Any], result: CaptureAdapterResult, capture_dir: Path
) -> dict[str, Any]:
    if result.status not in TERMINAL_STATUSES:
        raise VisualCaptureError("adapter returned invalid visual capture status")
    reason_codes = _stable_reason_codes(result.reason_codes)
    if reason_codes and any(code in INTERACTION_REASON_CODES for code in reason_codes):
        status = "INTERACTION_REQUIRED"
    else:
        status = result.status
    frames = []
    contact_sheet_hash = None
    contact_sheet_path = capture_dir / CONTACT_SHEET_NAME
    if result.frames:
        _write_private_bytes_atomic(contact_sheet_path, b"".join(frame.image_bytes for frame in result.frames))
        contact_sheet_hash = _sha256_file(contact_sheet_path)
    for index, frame in enumerate(result.frames):
        drift = abs(frame.observed_time_seconds - frame.requested_time_seconds)
        image_name = f"frame-{index:02d}-{frame.offset_seconds:+d}.private.bin"
        image_path = capture_dir / image_name
        _write_private_bytes_atomic(image_path, frame.image_bytes)
        frame_reason_codes = []
        if drift > MAX_DRIFT_SECONDS:
            frame_reason_codes.append("timestamp_drift_exceeded")
            status = "NEEDS_RECAPTURE"
        frames.append(
            {
                "index": index,
                "offset_seconds": frame.offset_seconds,
                "requested_time_seconds": frame.requested_time_seconds,
                "observed_time_seconds": frame.observed_time_seconds,
                "drift_seconds": round(drift, 6),
                "width": frame.width,
                "height": frame.height,
                "sha256": _sha256_file(image_path),
                "private_artifact": image_name,
                "reason_codes": frame_reason_codes,
            }
        )
    if status == "CAPTURED" and not frames:
        status = "FAILED_RETRYABLE"
        reason_codes = _stable_reason_codes((*reason_codes, "no_frames_captured"))
    return {
        "schema": MANIFEST_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "action_id": job["action_id"],
        "task_ref": job["task_ref"],
        "status": status,
        "provider": job["provider"],
        "normalized_url": job["normalized_url"],
        "capture_mode": job["capture_mode"],
        "requested_time_seconds": job["requested_time_seconds"],
        "offsets_seconds": list(job["offsets_seconds"]),
        "frames": frames,
        "frame_count": len(frames),
        "contact_sheet": {
            "private_artifact": CONTACT_SHEET_NAME if contact_sheet_hash else None,
            "sha256": contact_sheet_hash,
        },
        "reason_codes": reason_codes,
        "retryable": bool(result.retryable or status == "FAILED_RETRYABLE"),
        "human_review_required": bool(
            job.get("human_review_required")
            or result.human_review_required
            or status in {"HUMAN_REVIEW_PENDING", "NEEDS_RECAPTURE"}
        ),
        "evidence_state": "private_manifest_only",
    }


def _receipt_from_manifest(manifest: dict[str, Any], manifest_hash: str) -> dict[str, Any]:
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "action_id": manifest["action_id"],
        "task_ref": manifest["task_ref"],
        "status": manifest["status"],
        "frame_count": manifest["frame_count"],
        "manifest_hash": manifest_hash,
        "capture_mode": manifest["capture_mode"],
        "reason_codes": list(manifest["reason_codes"]),
        "retryable": bool(manifest["retryable"]),
        "human_review_required": bool(manifest["human_review_required"]),
        "stale": False,
    }
    _assert_public_receipt_is_sanitized(receipt)
    return receipt


def _assert_public_receipt_is_sanitized(receipt: dict[str, Any]) -> None:
    if set(receipt) != {
        "schema",
        "action_id",
        "task_ref",
        "status",
        "frame_count",
        "manifest_hash",
        "capture_mode",
        "reason_codes",
        "retryable",
        "human_review_required",
        "stale",
    }:
        raise VisualCaptureError("public receipt field set is not sanitized")
    rendered = json.dumps(receipt, sort_keys=True)
    blocked = ("youtube", "http://", "https://", "/", "\\", "profile", "stdout", "stderr")
    if any(marker in rendered.lower() for marker in blocked):
        raise VisualCaptureError("public receipt leaked private capture data")


def _claim_one_job(spool_root: Path) -> Path | None:
    _prepare_private_directory(spool_root)
    incoming = spool_root / "queued"
    claimed = spool_root / "claimed"
    _prepare_private_directory(incoming)
    _prepare_private_directory(claimed)
    jobs = sorted(path for path in incoming.glob("*.json") if path.is_file() and not path.is_symlink())
    if not jobs:
        return None
    source = _ensure_child_path(jobs[0], incoming)
    target = claimed / source.name
    return _move_job(source, target)


def _read_job(job_path: Path) -> dict[str, Any]:
    if job_path.is_symlink():
        raise VisualCaptureError("visual capture job path may not be a symlink")
    with job_path.open("r", encoding="utf-8") as handle:
        decoded = json.load(handle)
    if not isinstance(decoded, dict):
        raise VisualCaptureError("visual capture job JSON must be an object")
    return decoded


def _move_job(source: Path, target: Path) -> Path:
    _prepare_private_directory(target.parent)
    if target.exists():
        target.unlink()
    source.replace(target)
    _owner_only_file(target)
    return target


def _capture_dir(artifact_root: Path, job: dict[str, Any]) -> Path:
    digest = _sha256_bytes(
        json.dumps(
            {
                "action_id": job["action_id"],
                "task_ref": job["task_ref"],
                "provider": job["provider"],
                "normalized_url": job["normalized_url"],
                "requested_time_seconds": job["requested_time_seconds"],
                "offsets_seconds": list(job["offsets_seconds"]),
                "capture_mode": job["capture_mode"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )[:16]
    return _ensure_child_path(
        artifact_root / f"{job['action_id']}-{job['task_ref']}-{digest}",
        artifact_root,
    )


def _safe_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise VisualCaptureError(f"{field} is invalid")
    token = _SAFE_ID_RE.sub("-", value).strip(".-")
    if token != value or not token:
        raise VisualCaptureError(f"{field} is invalid")
    return token


def _required_env_path(env: dict[str, str], key: str) -> Path:
    value = env.get(key, "").strip()
    if not value:
        raise VisualCaptureError(f"{key} is required")
    return Path(value).expanduser()


def _optional_fixed_executable(env: dict[str, str], key: str) -> Path | None:
    value = env.get(key, "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise VisualCaptureError(f"{key} must be an absolute private runtime path")
    return path


def _validate_private_root(path: Path, label: str) -> None:
    for candidate in (path, *path.parents):
        if candidate.exists() and candidate.is_symlink():
            raise VisualCaptureError(f"{label} may not traverse a symlink")
    resolved = path.resolve(strict=False)
    repo = ROOT.resolve(strict=False)
    if resolved == repo or _path_is_relative_to(resolved, repo):
        raise VisualCaptureError(f"{label} must be outside the public repository")


def _ensure_child_path(path: Path, root: Path) -> Path:
    root_resolved = root.resolve(strict=False)
    candidate = path.resolve(strict=False)
    if candidate == root_resolved:
        return candidate
    if not _path_is_relative_to(candidate, root_resolved):
        raise VisualCaptureError("visual capture path escapes its private root")
    return candidate


def _prepare_private_directory(path: Path) -> None:
    _validate_private_root(path, "visual capture private directory")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)


def _write_private_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _write_private_bytes_atomic(path, rendered.encode("utf-8"))


def _write_private_bytes_atomic(path: Path, payload: bytes) -> None:
    _prepare_private_directory(path.parent)
    _ensure_child_path(path, path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        _owner_only_file(tmp_path)
        tmp_path.replace(path)
        _owner_only_file(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _read_public_receipt(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        decoded = json.load(handle)
    if not isinstance(decoded, dict):
        raise VisualCaptureError("stored public receipt is invalid")
    _assert_public_receipt_is_sanitized(decoded)
    return decoded


def _owner_only_file(path: Path) -> None:
    if os.name == "posix":
        path.chmod(0o600)


def _delete_temporary_paths(paths: tuple[Path, ...], root: Path) -> None:
    for path in paths:
        target = _ensure_child_path(path, root)
        if target.exists() and target.is_file():
            target.unlink()
        elif target.exists() and target.is_dir():
            shutil.rmtree(target)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_reason_codes(values: tuple[str, ...]) -> list[str]:
    clean = []
    for value in values:
        if not isinstance(value, str) or re.fullmatch(r"[a-z0-9_]{1,80}", value) is None:
            raise VisualCaptureError("reason code is invalid")
        clean.append(value)
    return sorted(set(clean))


def _empty_receipt() -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "action_id": "home_edge_visual_capture_tick",
        "task_ref": "none",
        "status": "QUEUED",
        "frame_count": 0,
        "manifest_hash": None,
        "capture_mode": "background",
        "reason_codes": ["no_queued_job"],
        "retryable": False,
        "human_review_required": False,
        "stale": False,
    }


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "CaptureAdapterResult",
    "CapturedFrame",
    "DEFAULT_OFFSETS_SECONDS",
    "JOB_SCHEMA",
    "MANIFEST_SCHEMA",
    "RECEIPT_SCHEMA",
    "VisualCaptureError",
    "VisualCaptureRuntimeConfig",
    "process_one_visual_capture_job",
    "run_visual_capture_job",
    "runtime_config_from_env",
    "validate_visual_capture_job",
]
