from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
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
CHROME_EXECUTABLE_NAMES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
)
PLAYER_SELECTORS = (
    "video.html5-main-video",
    "#movie_player video",
    "#player video",
)
PLAYER_REGION_SELECTORS = (
    "#movie_player",
    "#player-container",
    "#player",
    "video.html5-main-video",
)
INTERACTION_SELECTORS = {
    "consent_required": (
        "form[action*='consent.youtube.com']",
        "iframe[src*='consent.youtube.com']",
        "text=/Before you continue/i",
    ),
    "login_required": (
        "a[href*='ServiceLogin']",
        "tp-yt-paper-button[aria-label*='Sign in']",
        "text=/Sign in to confirm/i",
    ),
    "age_check_required": (
        "text=/Sign in to confirm your age/i",
        "text=/age-restricted/i",
    ),
    "cookie_prompt_required": (
        "button:has-text('Accept all')",
        "button:has-text('Reject all')",
        "text=/cookies/i",
    ),
}
PLAYWRIGHT_TIMEOUT_MS = 15_000
FRAME_STABLE_POLLS = 3
FRAME_STABLE_INTERVAL_MS = 250
SUBPROCESS_TIMEOUT_SECONDS = 120


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
    """Bounded YouTube player capture using fixed browser and media routes."""

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
        browser_result = self._capture_with_browser(
            job,
            normalized_url=normalized_url,
            config=config,
        )
        if browser_result.status != "FAILED_RETRYABLE" or browser_result.frames:
            return browser_result
        if (
            "playwright_unavailable" not in browser_result.reason_codes
            and "chrome_runtime_missing" not in browser_result.reason_codes
        ):
            return browser_result
        if config.yt_dlp_path is not None and config.ffmpeg_path is not None:
            fallback = self._capture_with_media_fallback(
                job,
                normalized_url=normalized_url,
                config=config,
                output_dir=output_dir,
            )
            if fallback.status != "FAILED_RETRYABLE" or fallback.frames:
                return fallback
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=_merge_reason_codes(
                    browser_result.reason_codes,
                    fallback.reason_codes,
                ),
                retryable=True,
                temporary_paths=fallback.temporary_paths,
            )
        return browser_result

    def _capture_with_browser(
        self,
        job: dict[str, Any],
        *,
        normalized_url: str,
        config: VisualCaptureRuntimeConfig,
    ) -> CaptureAdapterResult:
        chrome = _fixed_chrome_executable()
        if chrome is None:
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("chrome_runtime_missing",),
                retryable=True,
            )
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception:
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("playwright_unavailable",),
                retryable=True,
            )

        frames: list[CapturedFrame] = []
        headless = job["capture_mode"] != "visible_kiosk"
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(config.browser_profile),
                    executable_path=str(chrome),
                    headless=headless,
                    args=("--no-first-run", "--disable-background-networking"),
                    viewport={"width": 1280, "height": 720},
                    device_scale_factor=1,
                )
                try:
                    page = context.new_page()
                    page.goto(
                        normalized_url,
                        wait_until="domcontentloaded",
                        timeout=PLAYWRIGHT_TIMEOUT_MS,
                    )
                    interaction = _detect_interaction_state(page)
                    if interaction is not None:
                        return CaptureAdapterResult(
                            status="INTERACTION_REQUIRED",
                            reason_codes=(interaction,),
                        )
                    video = _first_visible_locator(page, PLAYER_SELECTORS)
                    if video is None:
                        return CaptureAdapterResult(
                            status="NOT_VISIBLE",
                            reason_codes=("player_not_visible",),
                            retryable=True,
                        )
                    player = _first_visible_locator(page, PLAYER_REGION_SELECTORS) or video
                    for offset in job["offsets_seconds"]:
                        target = max(0.0, job["requested_time_seconds"] + offset)
                        _seek_pause_video(page, target)
                        observed = _wait_for_stable_decoded_frame(page)
                        screenshot = player.screenshot(
                            type="png",
                            timeout=PLAYWRIGHT_TIMEOUT_MS,
                        )
                        box = player.bounding_box() or {}
                        frames.append(
                            CapturedFrame(
                                offset_seconds=offset,
                                requested_time_seconds=target,
                                observed_time_seconds=observed,
                                width=int(box.get("width") or 0),
                                height=int(box.get("height") or 0),
                                image_bytes=screenshot,
                            )
                        )
                finally:
                    context.close()
        except PlaywrightTimeoutError:
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("browser_timeout",),
                retryable=True,
            )
        except Exception:
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("browser_capture_failed",),
                retryable=True,
            )

        if not frames:
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("no_frames_captured",),
                retryable=True,
            )
        return CaptureAdapterResult(status="CAPTURED", frames=tuple(frames))

    def _capture_with_media_fallback(
        self,
        job: dict[str, Any],
        *,
        normalized_url: str,
        config: VisualCaptureRuntimeConfig,
        output_dir: Path,
    ) -> CaptureAdapterResult:
        temp_dir = Path(tempfile.mkdtemp(prefix="media-fallback-", dir=output_dir))
        frames: list[CapturedFrame] = []
        try:
            clip_template = temp_dir / "clip.%(ext)s"
            first_time = max(
                0.0,
                min(job["requested_time_seconds"] + min(job["offsets_seconds"]), 86400.0),
            )
            last_time = max(
                first_time + 1.0,
                min(
                    job["requested_time_seconds"] + max(job["offsets_seconds"]) + 1.0,
                    86400.0,
                ),
            )
            ytdlp_cmd = [
                str(config.yt_dlp_path),
                "--no-playlist",
                "--no-progress",
                "--merge-output-format",
                "mp4",
                "--download-sections",
                f"*{first_time:.3f}-{last_time:.3f}",
                "-f",
                "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                "-o",
                str(clip_template),
                normalized_url,
            ]
            ytdlp = subprocess.run(
                ytdlp_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
                shell=False,
            )
            if ytdlp.returncode != 0:
                return CaptureAdapterResult(
                    status="FAILED_RETRYABLE",
                    reason_codes=("fallback_download_failed",),
                    retryable=True,
                    temporary_paths=(temp_dir,),
                )
            clip = _single_child_file(temp_dir)
            if clip is None or not clip.exists() or clip.stat().st_size <= 0:
                return CaptureAdapterResult(
                    status="FAILED_RETRYABLE",
                    reason_codes=("fallback_download_failed",),
                    retryable=True,
                    temporary_paths=(temp_dir,),
                )
            for index, offset in enumerate(job["offsets_seconds"]):
                target = max(0.0, job["requested_time_seconds"] + offset)
                seek_seconds = max(0.0, target - first_time)
                image_path = temp_dir / f"frame-{index:02d}.png"
                if image_path.exists():
                    image_path.unlink()
                ffmpeg_cmd = [
                    str(config.ffmpeg_path),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{seek_seconds:.3f}",
                    "-i",
                    str(clip),
                    "-frames:v",
                    "1",
                    "-y",
                    str(image_path),
                ]
                ffmpeg = subprocess.run(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=SUBPROCESS_TIMEOUT_SECONDS,
                    shell=False,
                )
                if (
                    ffmpeg.returncode != 0
                    or not image_path.exists()
                    or image_path.stat().st_size <= 0
                ):
                    return CaptureAdapterResult(
                        status="FAILED_RETRYABLE",
                        reason_codes=("fallback_frame_extract_failed",),
                        retryable=True,
                        temporary_paths=(temp_dir,),
                    )
                payload = image_path.read_bytes()
                width, height = _image_dimensions(payload)
                frames.append(
                    CapturedFrame(
                        offset_seconds=offset,
                        requested_time_seconds=target,
                        observed_time_seconds=target,
                        width=width,
                        height=height,
                        image_bytes=payload,
                    )
                )
        except Exception:
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("fallback_capture_failed",),
                retryable=True,
                temporary_paths=(temp_dir,),
            )
        if not frames:
            return CaptureAdapterResult(
                status="FAILED_RETRYABLE",
                reason_codes=("fallback_no_frames",),
                retryable=True,
                temporary_paths=(temp_dir,),
            )
        return CaptureAdapterResult(
            status="HUMAN_REVIEW_PENDING",
            frames=tuple(frames),
            reason_codes=("fallback_used",),
            human_review_required=True,
            temporary_paths=(temp_dir,),
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
        manifest_hash = _sha256_file(manifest_path)
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
    allowed = required | {"offsets_seconds", "capture_mode", "human_review_required"}
    if set(job) - allowed:
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
    return {
        "schema": JOB_SCHEMA,
        "action_id": action_id,
        "task_ref": task_ref,
        "provider": "youtube",
        "normalized_url": _normalize_youtube_watch_url(job.get("url")),
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
    return urlunparse(("https", "www.youtube.com", "/watch", "", urlencode({"v": video_ids[0]}), ""))


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
    status = "INTERACTION_REQUIRED" if any(code in INTERACTION_REASON_CODES for code in reason_codes) else result.status
    frames = []
    contact_sheet_hash = None
    contact_sheet_path = capture_dir / CONTACT_SHEET_NAME
    if result.frames:
        _write_private_bytes_atomic(contact_sheet_path, _contact_sheet_png(result.frames))
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
    return _move_job(_ensure_child_path(jobs[0], incoming), claimed / jobs[0].name)


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


def _fixed_chrome_executable() -> Path | None:
    for name in CHROME_EXECUTABLE_NAMES:
        resolved = shutil.which(name)
        if not resolved:
            continue
        path = Path(resolved)
        try:
            if path.is_file() and os.access(path, os.X_OK):
                return path
        except OSError:
            continue
    return None


def _detect_interaction_state(page: Any) -> str | None:
    for reason, selectors in INTERACTION_SELECTORS.items():
        if _first_visible_locator(page, selectors) is not None:
            return reason
    return None


def _first_visible_locator(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible(timeout=1000):
                return locator
        except Exception:
            continue
    return None


def _seek_pause_video(page: Any, target_seconds: float) -> None:
    page.evaluate(
        """seconds => {
            const video = document.querySelector('video.html5-main-video, #movie_player video, #player video');
            if (!video) {
                throw new Error('player_video_missing');
            }
            video.currentTime = seconds;
            video.pause();
        }""",
        target_seconds,
    )


def _wait_for_stable_decoded_frame(page: Any) -> float:
    stable = 0
    last: tuple[int, float] | None = None
    observed = 0.0
    for _ in range(20):
        state = page.evaluate(
            """() => {
                const video = document.querySelector('video.html5-main-video, #movie_player video, #player video');
                if (!video) {
                    throw new Error('player_video_missing');
                }
                const quality = typeof video.getVideoPlaybackQuality === 'function'
                    ? video.getVideoPlaybackQuality()
                    : {};
                return {
                    decoded: quality.totalVideoFrames || video.webkitDecodedFrameCount || 0,
                    currentTime: video.currentTime || 0
                };
            }"""
        )
        decoded = int(state.get("decoded") or 0)
        observed = float(state.get("currentTime") or 0.0)
        current = (decoded, round(observed, 3))
        if decoded > 0 and current == last:
            stable += 1
            if stable >= FRAME_STABLE_POLLS:
                return observed
        else:
            stable = 1
            last = current
        page.wait_for_timeout(FRAME_STABLE_INTERVAL_MS)
    return observed


def _single_child_file(root: Path) -> Path | None:
    files = sorted(path for path in root.iterdir() if path.is_file() and not path.is_symlink())
    if not files:
        return None
    return _ensure_child_path(files[0], root)


def _image_dimensions(payload: bytes) -> tuple[int, int]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        width, height = struct.unpack(">II", payload[16:24])
        return int(width), int(height)
    try:
        from PIL import Image
    except Exception as exc:
        raise VisualCaptureError("Pillow runtime dependency is required") from exc
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        handle.write(payload)
        handle.flush()
        with Image.open(handle.name) as image:
            return int(image.width), int(image.height)


def _contact_sheet_png(frames: tuple[CapturedFrame, ...]) -> bytes:
    try:
        from PIL import Image
    except Exception:
        return _stdlib_contact_sheet_png(frames)
    decoded = []
    for frame in frames:
        with tempfile.NamedTemporaryFile(suffix=".png") as handle:
            handle.write(frame.image_bytes)
            handle.flush()
            with Image.open(handle.name) as image:
                decoded.append(image.convert("RGB"))
    if not decoded:
        raise VisualCaptureError("contact sheet requires at least one frame")
    width = max(image.width for image in decoded)
    height = sum(image.height for image in decoded)
    sheet = Image.new("RGB", (width, height), "black")
    y = 0
    for image in decoded:
        sheet.paste(image, (0, y))
        y += image.height
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        sheet.save(handle.name, format="PNG")
        handle.seek(0)
        return handle.read()


def _stdlib_contact_sheet_png(frames: tuple[CapturedFrame, ...]) -> bytes:
    if not frames:
        raise VisualCaptureError("contact sheet requires at least one frame")
    width = max(1, len(frames))
    pixels = bytearray()
    for frame in frames:
        pixels.extend(hashlib.sha256(frame.image_bytes).digest()[:3])
    return _rgb_png(width, 1, bytes(pixels))


def _rgb_png(width: int, height: int, pixels: bytes) -> bytes:
    if len(pixels) != width * height * 3:
        raise VisualCaptureError("PNG pixel buffer has invalid size")

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = bytearray()
    stride = width * 3
    for y in range(height):
        rows.append(0)
        start = y * stride
        rows.extend(pixels[start : start + stride])
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(bytes(rows))),
            chunk(b"IEND", b""),
        )
    )


def _merge_reason_codes(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({value for group in groups for value in group}))


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
    "BrowserFirstVisualCaptureAdapter",
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
