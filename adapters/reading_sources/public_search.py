from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen

from core.reading.public_sources import PublicReadingSourceError, ReadingSourceResult


FetchText = Callable[[str], str]


def build_default_public_reading_adapters(fetch_text: FetchText | None = None) -> tuple[object, ...]:
    fetcher = fetch_text or _urllib_fetch_text
    return (
        FourReadPublicSearchAdapter(fetcher),
        AknigaPublicSearchAdapter(fetcher),
        KnigavuhePublicSearchAdapter(fetcher),
    )


@dataclass(frozen=True)
class _SourceConfig:
    source_id: str
    base_url: str
    search_path: str
    media_kind: str


class _PublicSearchAdapter:
    config: _SourceConfig

    def __init__(self, fetch_text: FetchText | None = None) -> None:
        self._fetch_text = fetch_text or _urllib_fetch_text

    @property
    def source_id(self) -> str:
        return self.config.source_id

    def search(self, query: str, *, limit: int = 10) -> tuple[ReadingSourceResult, ...]:
        url = self._search_url(query)
        try:
            html = self._fetch_text(url)
        except Exception as exc:
            raise PublicReadingSourceError(self.source_id, "READING_SOURCE_FETCH_FAILED", "public metadata search failed") from exc
        parser = _ReadingResultParser(self.config)
        parser.feed(html)
        return tuple(parser.results[: max(0, limit)])

    def _search_url(self, query: str) -> str:
        return urljoin(self.config.base_url, self.config.search_path.format(query=quote_plus(query)))


class FourReadPublicSearchAdapter(_PublicSearchAdapter):
    config = _SourceConfig(
        source_id="public.4read.org",
        base_url="https://4read.org/",
        search_path="search?q={query}",
        media_kind="ebook",
    )


class AknigaPublicSearchAdapter(_PublicSearchAdapter):
    config = _SourceConfig(
        source_id="public.akniga.org",
        base_url="https://akniga.org/",
        search_path="search/?q={query}",
        media_kind="audiobook",
    )


class KnigavuhePublicSearchAdapter(_PublicSearchAdapter):
    config = _SourceConfig(
        source_id="public.knigavuhe.org",
        base_url="https://knigavuhe.org/",
        search_path="search/?q={query}",
        media_kind="audiobook",
    )


class _ReadingResultParser(HTMLParser):
    def __init__(self, config: _SourceConfig) -> None:
        super().__init__(convert_charrefs=True)
        self._config = config
        self.results: list[ReadingSourceResult] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        if "data-reading-result" not in attr:
            return
        title = attr.get("data-title", "")
        authors = tuple(value.strip() for value in attr.get("data-authors", attr.get("data-author", "")).split(";") if value.strip())
        href = attr.get("data-url") or attr.get("href")
        if not title or not authors or not href:
            return
        source_url = urljoin(self._config.base_url, href)
        source_result_id = attr.get("data-source-result-id") or source_url
        self.results.append(
            ReadingSourceResult(
                source_id=self._config.source_id,
                source_result_id=source_result_id,
                title=title,
                authors=authors,
                isbn=attr.get("data-isbn") or None,
                source_url=source_url,
                cover_url=urljoin(self._config.base_url, attr["data-cover"]) if attr.get("data-cover") else None,
                media_kind=attr.get("data-media-kind") or self._config.media_kind,
            )
        )


def _urllib_fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "SkeletonReadingMetadataSearch/1.0"})
    with urlopen(request, timeout=10) as response:
        content_type = response.headers.get_content_charset() or "utf-8"
        return response.read(512_000).decode(content_type, errors="replace")
