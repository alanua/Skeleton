from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.reading_sources import build_default_public_reading_adapters
from core.reading import NEUTRAL_COVER_PLACEHOLDER
from core.reading.public_sources import (
    PublicReadingSourceError,
    ReadingSourceResult,
    ReadingSourceSearchService,
    dedupe_reading_results,
    reading_search_response_to_dict,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "reading_sources"
SCHEMA_PATH = ROOT / "schemas" / "reading" / "public_source_search_response.schema.json"


def fixture_fetcher(url: str) -> str:
    if "4read.org" in url:
        return (FIXTURE_ROOT / "4read_search.html").read_text(encoding="utf-8")
    if "akniga.org" in url:
        return (FIXTURE_ROOT / "akniga_search.html").read_text(encoding="utf-8")
    if "knigavuhe.org" in url:
        return (FIXTURE_ROOT / "knigavuhe_search.html").read_text(encoding="utf-8")
    raise AssertionError(f"unexpected url: {url}")


def test_public_adapters_parse_normalized_metadata_and_source_ids() -> None:
    adapters = build_default_public_reading_adapters(fixture_fetcher)

    assert [adapter.source_id for adapter in adapters] == [
        "public.4read.org",
        "public.akniga.org",
        "public.knigavuhe.org",
    ]
    first_source_results = adapters[0].search("master margarita")

    assert first_source_results[0].title == "The Master and Margarita"
    assert first_source_results[0].authors == ("Mikhail Bulgakov",)
    assert first_source_results[0].source_url == "https://4read.org/book/master-and-margarita"
    assert first_source_results[0].cover_url == "https://img.4read.org/covers/master-margarita.jpg"


def test_search_isolates_source_health_and_dedupes_across_sources() -> None:
    class BrokenAdapter:
        source_id = "public.akniga.org"

        def search(self, query: str, *, limit: int = 10) -> tuple[ReadingSourceResult, ...]:
            raise PublicReadingSourceError(self.source_id, "READING_SOURCE_TIMEOUT", "timeout")

    adapters = build_default_public_reading_adapters(fixture_fetcher)
    service = ReadingSourceSearchService((adapters[0], BrokenAdapter(), adapters[2]))
    response = service.search("master margarita")
    payload = reading_search_response_to_dict(response)

    assert payload["source_health"] == [
        {
            "schema": "skeleton.reading.public_source_health.v1",
            "source_id": "public.4read.org",
            "status": "OK",
            "reason_code": None,
        },
        {
            "schema": "skeleton.reading.public_source_health.v1",
            "source_id": "public.akniga.org",
            "status": "BLOCKED",
            "reason_code": "READING_SOURCE_TIMEOUT",
        },
        {
            "schema": "skeleton.reading.public_source_health.v1",
            "source_id": "public.knigavuhe.org",
            "status": "OK",
            "reason_code": None,
        },
    ]
    master = payload["results"][0]
    assert master["dedupe_key"] == "isbn:9780141180144"
    assert master["matched_source_ids"] == ["public.4read.org", "public.knigavuhe.org"]


def test_safe_cover_enrichment_uses_only_unambiguous_same_work_covers() -> None:
    adapters = build_default_public_reading_adapters(fixture_fetcher)
    response = ReadingSourceSearchService(adapters).search("solaris")
    payload = reading_search_response_to_dict(response)
    by_title = {item["title"]: item for item in payload["results"]}

    master = by_title["The Master and Margarita"]
    solaris = by_title["Solaris"]
    assert master["cover_url"] == "https://img.4read.org/covers/master-margarita.jpg"
    assert master["cover_status"] == "source"
    assert solaris["cover_url"] == NEUTRAL_COVER_PLACEHOLDER
    assert solaris["cover_status"] == "placeholder"


def test_missing_cover_can_be_enriched_from_exact_high_confidence_match() -> None:
    missing_cover = ReadingSourceResult(
        source_id="public.4read.org",
        source_result_id="4read-roadside",
        title="Roadside Picnic",
        authors=("Arkady Strugatsky", "Boris Strugatsky"),
        isbn="9780026151702",
        source_url="https://4read.org/book/roadside-picnic",
        media_kind="ebook",
    )
    with_cover = ReadingSourceResult(
        source_id="public.akniga.org",
        source_result_id="akniga-roadside",
        title="Roadside Picnic",
        authors=("Arkady Strugatsky", "Boris Strugatsky"),
        source_url="https://akniga.org/audio/roadside-picnic",
        cover_url="https://akniga.org/covers/roadside.jpg",
        media_kind="audiobook",
    )

    result = dedupe_reading_results((missing_cover, with_cover))[0]

    assert result.cover_url == "https://akniga.org/covers/roadside.jpg"
    assert result.cover_status == "enriched"


def test_public_search_response_validates_against_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    response = ReadingSourceSearchService(build_default_public_reading_adapters(fixture_fetcher)).search("master")
    payload = reading_search_response_to_dict(response)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema).validate(payload)
