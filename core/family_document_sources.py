from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


SOURCE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
DEFAULT_INACTIVITY_SECONDS = 60


@dataclass(frozen=True)
class MfpSourceProfile:
    source_id: str
    profile_id: str
    intake_dir: Path | None = None
    inactivity_window_seconds: int = DEFAULT_INACTIVITY_SECONDS

    @property
    def identity(self) -> str:
        return f"{self.source_id}:{self.profile_id}"

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "profile_id": self.profile_id,
            "inactivity_window_seconds": self.inactivity_window_seconds,
        }


def _token(value: object, field: str) -> str:
    text = str(value or "")
    if SOURCE_TOKEN_RE.fullmatch(text) is None:
        raise ValueError(f"invalid_{field}")
    return text


def source_profile_from_mapping(value: Mapping[str, Any]) -> MfpSourceProfile:
    source_id = _token(value.get("source_id"), "source_id")
    profile_id = _token(value.get("profile_id"), "profile_id")
    raw_window = value.get("inactivity_window_seconds", DEFAULT_INACTIVITY_SECONDS)
    try:
        window = int(raw_window)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_inactivity_window_seconds") from exc
    if window < 1 or window > 3600:
        raise ValueError("invalid_inactivity_window_seconds")
    intake_dir = Path(str(value["intake_dir"])).expanduser() if value.get("intake_dir") else None
    return MfpSourceProfile(
        source_id=source_id,
        profile_id=profile_id,
        intake_dir=intake_dir,
        inactivity_window_seconds=window,
    )


def load_source_profiles(path: str | Path) -> list[MfpSourceProfile]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("source_config_not_object")
    profiles = raw.get("mfp_sources", [])
    if not isinstance(profiles, list):
        raise ValueError("mfp_sources_not_list")
    return [source_profile_from_mapping(item) for item in profiles if isinstance(item, Mapping)]


def discover_source_artifacts(profile: MfpSourceProfile) -> list[Path]:
    if profile.intake_dir is None:
        return []
    root = profile.intake_dir.expanduser().resolve(strict=False)
    if not root.is_dir():
        return []
    suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
    paths = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in suffixes]
    return sorted(paths, key=lambda item: (item.stat().st_mtime_ns, item.name))


def iter_configured_artifacts(profiles: Iterable[MfpSourceProfile]) -> Iterable[tuple[MfpSourceProfile, Path]]:
    for profile in profiles:
        for path in discover_source_artifacts(profile):
            yield profile, path
