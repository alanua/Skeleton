# Reading Sources

Skeleton reading sources are public-safe metadata search adapters. They may discover
titles, authors, source result URLs, ISBNs, media kind, and cover URLs that are
already visible in public search pages. They must not automate authentication,
CAPTCHA handling, audiobook or file download, mirroring, DRM bypass, or bulk
crawling.

## Source IDs

- `public.4read.org`
- `public.akniga.org`
- `public.knigavuhe.org`

## Normalized Response

`core.reading.public_sources.ReadingSourceSearchService` searches each configured
adapter independently. A failing source is reported in `source_health` as
`BLOCKED` and does not prevent healthy sources from returning results.

Results use `skeleton.reading.public_source_result.v1` and are deduplicated across
sources. ISBN matches are preferred. When ISBN is absent, exact normalized
title-plus-author matching is used as the high-confidence fallback.

Cover enrichment is intentionally conservative. If the selected canonical result
has no cover, another source in the same dedupe group may supply one only when the
group has a single ISBN or an exact normalized title-plus-author match and exactly
one candidate cover. Ambiguous cover candidates fall back to
`skeleton://reading/cover-placeholder/neutral`.

The checked-in schema is
`schemas/reading/public_source_search_response.schema.json`.
