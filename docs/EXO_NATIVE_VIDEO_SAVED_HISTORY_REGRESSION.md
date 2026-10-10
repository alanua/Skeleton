# Exo Native Video Saved History Regression

This regression covers the native Kotlin Compose video picker after the saved-library merge repair.

## Fixed behavior

- The TV target loads the complete saved work library from `GET /api/video/history` and merges it with endpoint watched history, keyed by stable `history_id`, then `history_key`, then `content_key`.
- Saved TV works open through `POST /api/video/history/open`, then apply the returned job and preference through `PUT /api/media/endpoints/home_edge_tv/video/selection`.
- TV saved-work selection does not call playback, mode, or handoff APIs.
- Samsung keeps endpoint-scoped watched-history semantics and does not receive TV saved-library selection.
- Polling only accepts canonical endpoint selections with matching endpoint, job, and source IDs, so a stale single-season job cannot overwrite a newer confirmed user selection.
- Failed saved-library or watched-history reads preserve the previously visible picker entries instead of replacing the list with an empty network failure result.
- Metadata-only loaded seasons no longer produce a false loaded state when the current job has no sources. The UI can retry through merged history open for TV saved works or reports the unavailable state honestly.
- Failed saved-library reads do not clear the existing history list; successful endpoint history remains a fallback.

## Synthetic coverage

`NativeVideoHistoryTest` verifies:

- Babylon Berlin remains visible when it exists only in saved library while Perry Mason exists in watched history.
- Stable saved IDs are sent to `history/open`, and returned selection is endpoint-scoped to `home_edge_tv`.
- A legacy S2-only job fails merged-readiness when metadata says S1 and S2 are loaded, while a merged S1+S2 job passes.
- Metadata without sources does not count as loaded source availability.
- Samsung and TV canonical selection identities stay isolated.
- Failed network reads preserve existing native history, and Samsung continues to use watched-history scope instead of the TV saved library.
