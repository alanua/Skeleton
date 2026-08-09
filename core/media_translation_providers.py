from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
import re
import urllib.request

from core.media_translation_monitor import (
    EvidenceStrength,
    LocalizationCapability,
    LocalizationObservation,
    MediaIdentity,
)


Fetcher = Callable[[str], str]

_UA_TERMS = ("українська", "украинская", "ukrainian", "uk", "ua")
_DUB_TERMS = ("дубляж", "dub")
_AUDIO_TERMS = ("озвуч", "audio", "voiceover", "voice-over")
_SUB_TERMS = ("субтит", "subtitle", "subtitles")
_ANNOUNCE_TERMS = ("скоро", "анонс", "trailer", "announce", "coming soon")


@dataclass(frozen=True)
class PublicMetadataPageAdapter:
    provider: str
    url_template: str
    fetcher: Fetcher | None = None

    def observations_for(
        self, identity: MediaIdentity, release_key: str
    ) -> tuple[LocalizationObservation, ...]:
        url = self.url_template.format(tmdb_id=identity.tmdb_id, release_key=release_key)
        try:
            page = self._fetch(url)
        except Exception:
            return ()
        return classify_public_metadata(self.provider, release_key, page)

    def _fetch(self, url: str) -> str:
        if self.fetcher is not None:
            return self.fetcher(url)
        request = urllib.request.Request(url, headers={"User-Agent": "SkeletonMediaMonitor/1.0"})
        with urllib.request.urlopen(request, timeout=8) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/" not in content_type and "json" not in content_type:
                return ""
            return response.read(256_000).decode("utf-8", errors="replace")


def kinobaza_adapter(fetcher: Fetcher | None = None) -> PublicMetadataPageAdapter:
    return PublicMetadataPageAdapter(
        "kinobaza",
        "https://kinobaza.com.ua/tmdb/{tmdb_id}",
        fetcher=fetcher,
    )


def official_streaming_adapter(provider: str, url_template: str, fetcher: Fetcher | None = None) -> PublicMetadataPageAdapter:
    if provider not in {"megogo", "sweet.tv", "takflix", "netflix", "apple"}:
        raise ValueError("provider is not an allowlisted public metadata source")
    return PublicMetadataPageAdapter(provider, url_template, fetcher=fetcher)


def opensubtitles_compatible_adapter(fetcher: Fetcher | None = None) -> PublicMetadataPageAdapter:
    return PublicMetadataPageAdapter(
        "opensubtitles",
        "https://www.opensubtitles.org/en/search/sublanguageid-ukr/imdbid-{tmdb_id}",
        fetcher=fetcher,
    )


def classify_public_metadata(
    provider: str, release_key: str, payload: str
) -> tuple[LocalizationObservation, ...]:
    text = _plain_text(payload)
    if release_key.lower() not in text and not any(term in text for term in _UA_TERMS):
        return ()
    announced = any(term in text for term in _ANNOUNCE_TERMS)
    strength = EvidenceStrength.ANNOUNCEMENT if announced else EvidenceStrength.HIGH
    observations: list[LocalizationObservation] = []
    if any(term in text for term in _DUB_TERMS):
        observations.append(
            LocalizationObservation(provider, release_key, LocalizationCapability.UK_DUB, not announced, strength)
        )
    if any(term in text for term in _AUDIO_TERMS):
        observations.append(
            LocalizationObservation(provider, release_key, LocalizationCapability.UK_AUDIO, not announced, strength)
        )
    if any(term in text for term in _SUB_TERMS):
        observations.append(
            LocalizationObservation(provider, release_key, LocalizationCapability.UK_SUBTITLES, not announced, strength)
        )
    if provider == "opensubtitles":
        observations = [
            item for item in observations if item.capability is LocalizationCapability.UK_SUBTITLES
        ]
    return tuple(observations)


def _plain_text(payload: str) -> str:
    parser = _TextParser()
    parser.feed(payload[:256_000])
    text = " ".join(parser.parts) if parser.parts else payload
    return re.sub(r"\s+", " ", text).lower()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
