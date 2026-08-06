from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class VideoProviderRegistryError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_KINDS = frozenset({"ocr", "transcription", "model", "extractor"})
_CLOUD_MARKERS = (
    "api.openai.com",
    "anthropic.com",
    "googleapis.com",
    "azure.com",
    "aws.amazon.com",
    "http://",
    "https://",
)


@dataclass(frozen=True)
class LocalProvider:
    name: str
    kind: str
    executable: Path
    argv_prefix: tuple[str, ...]
    max_input_bytes: int
    timeout_seconds: int
    stable_reason_code: str

    def __post_init__(self) -> None:
        if _NAME_RE.fullmatch(self.name) is None:
            raise VideoProviderRegistryError("PROVIDER_NAME_INVALID", "provider name is invalid")
        if self.kind not in _KINDS:
            raise VideoProviderRegistryError("PROVIDER_KIND_INVALID", "provider kind is invalid")
        if not isinstance(self.executable, Path) or not self.executable.is_absolute():
            raise VideoProviderRegistryError("PROVIDER_EXECUTABLE_INVALID", "provider executable must be absolute")
        if not self.argv_prefix or any(not isinstance(item, str) or not item for item in self.argv_prefix):
            raise VideoProviderRegistryError("PROVIDER_ARGV_INVALID", "provider argv prefix is invalid")
        if isinstance(self.max_input_bytes, bool) or self.max_input_bytes <= 0:
            raise VideoProviderRegistryError("PROVIDER_LIMIT_INVALID", "provider byte limit is invalid")
        if isinstance(self.timeout_seconds, bool) or self.timeout_seconds <= 0:
            raise VideoProviderRegistryError("PROVIDER_LIMIT_INVALID", "provider timeout is invalid")
        if _TOKEN_RE.fullmatch(self.stable_reason_code) is None:
            raise VideoProviderRegistryError("PROVIDER_REASON_INVALID", "provider reason code is invalid")
        haystack = " ".join((self.name, str(self.executable), *self.argv_prefix)).casefold()
        if any(marker in haystack for marker in _CLOUD_MARKERS):
            raise VideoProviderRegistryError("CLOUD_PROVIDER_FORBIDDEN", "cloud or network provider is forbidden")

    def command(self, input_path: Path, output_path: Path) -> tuple[str, ...]:
        if not input_path.is_absolute() or not output_path.is_absolute():
            raise VideoProviderRegistryError("PROVIDER_PATH_INVALID", "provider paths must be absolute")
        return tuple(str(item) for item in (self.executable, *self.argv_prefix, str(input_path), str(output_path)))


class LocalProviderRegistry:
    def __init__(self, providers: Iterable[LocalProvider]) -> None:
        self._providers: dict[tuple[str, str], LocalProvider] = {}
        for provider in providers:
            key = (provider.kind, provider.name)
            if key in self._providers:
                raise VideoProviderRegistryError("PROVIDER_DUPLICATE", "provider is duplicated")
            self._providers[key] = provider
        if not self._providers:
            raise VideoProviderRegistryError("PROVIDER_REGISTRY_EMPTY", "provider registry is empty")

    def require(self, kind: str, name: str) -> LocalProvider:
        if kind not in _KINDS or _NAME_RE.fullmatch(name) is None:
            raise VideoProviderRegistryError("PROVIDER_REQUEST_INVALID", "provider request is invalid")
        try:
            return self._providers[(kind, name)]
        except KeyError as exc:
            raise VideoProviderRegistryError("PROVIDER_NOT_ALLOWLISTED", "provider is not allowlisted") from exc

    def route(self, kind: str, preferred: str | None = None) -> LocalProvider:
        if preferred is not None:
            return self.require(kind, preferred)
        matches = [provider for (provider_kind, _), provider in self._providers.items() if provider_kind == kind]
        if len(matches) != 1:
            raise VideoProviderRegistryError("PROVIDER_ROUTE_AMBIGUOUS", "provider route must be explicit")
        return matches[0]

    def public_inventory(self) -> dict[str, object]:
        counts: dict[str, int] = {kind: 0 for kind in sorted(_KINDS)}
        for provider in self._providers.values():
            counts[provider.kind] += 1
        return {
            "schema": "skeleton.video_understanding.provider_inventory.v1",
            "local_only": True,
            "cloud_fallback": False,
            "provider_counts": counts,
        }


def synthetic_local_provider_registry(root: Path) -> LocalProviderRegistry:
    base = root.resolve(strict=False)
    return LocalProviderRegistry(
        (
            LocalProvider("tesseract-local", "ocr", base / "bin" / "tesseract", ("--local-only",), 2_000_000, 30, "LOCAL_OCR_READY"),
            LocalProvider("whisper-local", "transcription", base / "bin" / "whisper", ("--device", "local"), 20_000_000, 120, "LOCAL_TRANSCRIBER_READY"),
            LocalProvider("ollama-local", "model", base / "bin" / "ollama", ("run", "synthetic-video"), 120_000, 60, "LOCAL_MODEL_READY"),
            LocalProvider("ffmpeg-local", "extractor", base / "bin" / "ffmpeg", ("-nostdin",), 50_000_000, 90, "LOCAL_EXTRACTOR_READY"),
        )
    )
