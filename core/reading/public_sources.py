from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol
import re
import unicodedata
from urllib.parse import urlparse


READING_SOURCE_RESPONSE_SCHEMA = "skeleton.reading.public_source_search_response.v1"
READING_SOURCE_RESULT_SCHEMA = "skeleton.reading.public_source_result.v1"
READING_SOURCE_HEALTH_SCHEMA = "skeleton.reading.public_source_health.v1"
NEUTRAL_COVER_PLACEHOLDER = "skeleton://reading/cover-placeholder/neutral"

_ISBN_RE = re.compile(r"(?=(?:.*[0-9Xx]){10,13})[0-9Xx -]{10,20}")
_WORD_RE = re.compile(r"[a-z0-9]+")


class PublicReadingSourceError(RuntimeError):
    """Raised when one public metadata source cannot complete a search."""

    def __init__(self, source_id: str, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.source_id = source_id
        self.reason_code = reason_code


@dataclass(frozen=True)
class ReadingSourceResult:
    source_id: str
    source_result_id: str
    title: str
    authors: tuple[str, ...]
    source_url: str
    media_kind: str
    isbn: str | None = None
    cover_url: str | None = None
    cover_status: str = "source"
    dedupe_key: str | None = None
    matched_source_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReadingSourceHealth:
    source_id: str
    status: str
    reason_code: str | None = None


@dataclass(frozen=True)
class ReadingSourceSearchResponse:
    query: str
    results: tuple[ReadingSourceResult, ...]
    source_health: tuple[ReadingSourceHealth, ...]


class ReadingPublicSearchAdapter(Protocol):
    source_id: str

    def search(self, query: str, *, limit: int = 10) -> tuple[ReadingSourceResult, ...]:
        """Return public-safe metadata only; no content download is authorized."""


class ReadingSourceSearchService:
    """Search public reading metadata sources with per-source failure isolation."""

    def __init__(self, adapters: tuple[ReadingPublicSearchAdapter, ...]) -> None:
        source_ids = [adapter.source_id for adapter in adapters]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("reading source adapter ids must be unique")
        self._adapters = adapters

    def search(self, query: str, *, per_source_limit: int = 10, result_limit: int = 20) -> ReadingSourceSearchResponse:
        normalized_query = _require_query(query)
        raw_results: list[ReadingSourceResult] = []
        health: list[ReadingSourceHealth] = []
        for adapter in self._adapters:
            try:
                source_results = adapter.search(normalized_query, limit=per_source_limit)
            except PublicReadingSourceError as exc:
                health.append(ReadingSourceHealth(source_id=adapter.source_id, status="BLOCKED", reason_code=exc.reason_code))
                continue
            except Exception:
                health.append(
                    ReadingSourceHealth(
                        source_id=adapter.source_id,
                        status="BLOCKED",
                        reason_code="READING_SOURCE_UNEXPECTED_ERROR",
                    )
                )
                continue
            raw_results.extend(source_results)
            health.append(ReadingSourceHealth(source_id=adapter.source_id, status="OK"))

        deduped = dedupe_reading_results(tuple(raw_results))
        return ReadingSourceSearchResponse(
            query=normalized_query,
            results=tuple(deduped[: max(0, result_limit)]),
            source_health=tuple(health),
        )


def dedupe_reading_results(results: tuple[ReadingSourceResult, ...]) -> tuple[ReadingSourceResult, ...]:
    normalized_results = tuple(normalize_result(result) for result in results)
    isbn_key_by_title_author: dict[str, str | None] = {}
    for result in normalized_results:
        title_author = _title_author_key(result)
        key = f"isbn:{result.isbn}" if result.isbn else None
        if title_author not in isbn_key_by_title_author:
            isbn_key_by_title_author[title_author] = key
        elif key and isbn_key_by_title_author[title_author] not in (None, key):
            isbn_key_by_title_author[title_author] = None
        elif key:
            isbn_key_by_title_author[title_author] = key

    groups: dict[str, list[ReadingSourceResult]] = {}
    order: list[str] = []
    for normalized in normalized_results:
        key = _dedupe_key(normalized, isbn_key_by_title_author)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(normalized)

    merged = []
    for key in order:
        group = groups[key]
        winner = _best_metadata_record(group)
        cover = _safe_cover_for_group(group, winner)
        merged.append(
            replace(
                winner,
                cover_url=cover[0],
                cover_status=cover[1],
                dedupe_key=key,
                matched_source_ids=tuple(sorted({item.source_id for item in group})),
            )
        )
    return tuple(merged)


def normalize_result(result: ReadingSourceResult) -> ReadingSourceResult:
    source_url = _public_url_or_raise(result.source_url, "READING_SOURCE_URL_NOT_PUBLIC")
    cover_url = _optional_public_url(result.cover_url)
    isbn = normalize_isbn(result.isbn)
    title = _clean_text(result.title)
    authors = tuple(author for author in (_clean_text(value) for value in result.authors) if author)
    if not title or not authors:
        raise PublicReadingSourceError(result.source_id, "READING_SOURCE_METADATA_INCOMPLETE", "title and author are required")
    return replace(
        result,
        title=title,
        authors=authors,
        source_url=source_url,
        isbn=isbn,
        cover_url=cover_url,
        cover_status="source" if cover_url else "placeholder",
    )


def normalize_isbn(value: str | None) -> str | None:
    if not value:
        return None
    match = _ISBN_RE.search(value)
    if not match:
        return None
    compact = re.sub(r"[^0-9Xx]", "", match.group(0)).upper()
    if len(compact) not in (10, 13):
        return None
    return compact


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return " ".join(_WORD_RE.findall(ascii_text.lower()))


def reading_search_response_to_dict(response: ReadingSourceSearchResponse) -> dict[str, object]:
    return {
        "schema": READING_SOURCE_RESPONSE_SCHEMA,
        "query": response.query,
        "privacy_boundary": "PUBLIC_SAFE_WEB_METADATA_ONLY",
        "automated_downloads": False,
        "results": [_result_to_dict(result) for result in response.results],
        "source_health": [_health_to_dict(item) for item in response.source_health],
    }


def _dedupe_key(result: ReadingSourceResult, isbn_key_by_title_author: dict[str, str | None]) -> str:
    if result.isbn:
        return f"isbn:{result.isbn}"
    title_author = _title_author_key(result)
    isbn_key = isbn_key_by_title_author.get(title_author)
    if isbn_key:
        return isbn_key
    return f"title_author:{title_author}"


def _title_author_key(result: ReadingSourceResult) -> str:
    return f"{normalize_text(result.title)}|{'/'.join(normalize_text(author) for author in result.authors)}"


def _best_metadata_record(group: list[ReadingSourceResult]) -> ReadingSourceResult:
    return sorted(
        group,
        key=lambda item: (
            0 if item.isbn else 1,
            0 if item.cover_url else 1,
            item.source_id,
            item.title,
        ),
    )[0]


def _safe_cover_for_group(group: list[ReadingSourceResult], winner: ReadingSourceResult) -> tuple[str, str]:
    covers = sorted({item.cover_url for item in group if item.cover_url})
    if len(covers) > 1:
        return NEUTRAL_COVER_PLACEHOLDER, "placeholder"
    if winner.cover_url:
        return winner.cover_url, "source"
    if len(covers) == 1 and _cover_match_is_high_confidence(group):
        return covers[0], "enriched"
    return NEUTRAL_COVER_PLACEHOLDER, "placeholder"


def _cover_match_is_high_confidence(group: list[ReadingSourceResult]) -> bool:
    isbns = {item.isbn for item in group if item.isbn}
    if len(isbns) == 1:
        return True
    title_author_keys = {_title_author_key(item) for item in group}
    return len(title_author_keys) == 1


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _require_query(query: str) -> str:
    value = _clean_text(query)
    if len(value) < 2:
        raise ValueError("reading source query must contain at least two non-space characters")
    return value[:160]


def _optional_public_url(value: str | None) -> str | None:
    if not value:
        return None
    return _public_url_or_raise(value, "READING_COVER_URL_NOT_PUBLIC")


def _public_url_or_raise(value: str, reason_code: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(reason_code)
    return value


def _result_to_dict(result: ReadingSourceResult) -> dict[str, object]:
    return {
        "schema": READING_SOURCE_RESULT_SCHEMA,
        "source_id": result.source_id,
        "source_result_id": result.source_result_id,
        "title": result.title,
        "authors": list(result.authors),
        "isbn": result.isbn,
        "media_kind": result.media_kind,
        "source_url": result.source_url,
        "cover_url": result.cover_url or NEUTRAL_COVER_PLACEHOLDER,
        "cover_status": result.cover_status,
        "dedupe_key": result.dedupe_key,
        "matched_source_ids": list(result.matched_source_ids),
    }


def _health_to_dict(health: ReadingSourceHealth) -> dict[str, object]:
    return {
        "schema": READING_SOURCE_HEALTH_SCHEMA,
        "source_id": health.source_id,
        "status": health.status,
        "reason_code": health.reason_code,
    }
