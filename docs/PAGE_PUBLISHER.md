# Skeleton Page Publisher

Skeleton Page Publisher is the reusable controlled publication engine for validated static pages. Domain modules render page packages; Publisher validates, packages, encrypts when required, publishes, verifies, rolls back and returns a receipt.

## Ownership boundary

Skeleton owns:

- the publication manifest contract;
- renderer and downstream-action registries;
- package and privacy validation;
- deterministic builds and content hashes;
- publication profiles and route ownership;
- encryption for private-link pages;
- backend dispatch, verification, rollback and receipts.

Domain modules own templates, content schemas and domain actions. Travel may render an itinerary and register a Calendar action, but it does not implement GitHub Pages, Tailscale or filesystem deployment logic.

Ordinary page instances do **not** require an issue or pull request. Issues and PRs are only for changing publisher code, profiles, schemas or reusable templates.

## One-command pipeline

```bash
python -m core.page_pipeline build --manifest /private/path/page.yaml
python -m core.page_pipeline publish --manifest /private/path/page.yaml
```

Equivalent wrapper:

```bash
python scripts/publish_page.py publish --manifest /private/path/page.yaml
```

The normal flow is:

```text
module manifest
→ renderer adapter
→ isolated staging directory
→ privacy/assets/HTML validation
→ deterministic package and hashes
→ selected backend
→ local or HTTPS verification
→ durable state and rollback reference
→ downstream actions
→ publication receipt
```

An identical verified content hash returns `NO_CHANGE`. `update_owned` may modify only a route owned by the same profile, module and page ID.

## Manifest V1

Required fields:

```yaml
schema_version: 1
owner_module: travel
publication_profile_id: github_pages_encrypted_v1
page_id: milan-2026
template_id: travel_trip_v1
content_ref: /private/runtime/render-input
asset_manifest_ref: /private/runtime/assets.json
publication_mode: update_owned
operator_approval: publish_page_v1
```

Optional fields include `renderer_entrypoint`, `content_assets_ref`, `locale`, `selected_variant`, `stable_url`, `backend_options`, `downstream_actions`, `evidence_metadata` and `expires_at`.

Dynamic renderer entrypoints are disabled by default. Their module prefix must be explicitly listed in `SKELETON_PAGE_RENDERER_ALLOWLIST`. A safer in-process adapter can call `register_renderer()` before invoking the pipeline.

The machine contract is `schemas/page_publication_manifest_v1.schema.json`.

## Profiles and backends

### `travel_private_v1`

- owner: Travel;
- route namespace: `/travel`;
- backend: `tailscale_serve_static_v1`;
- visibility: tailnet-private;
- supports owned updates while preserving the existing exact route.

The backend reuses the existing bounded Tailscale status parser, fail-closed route inspection and HTTPS verification helpers.

### `filesystem_static_v1`

- owner: any registered module;
- route namespace: `/pages`;
- backend: `filesystem_static_v1`;
- publication root: manifest `backend_options.root` or `SKELETON_PAGE_FILESYSTEM_ROOT`;
- intended for local previews, tests and controlled static roots.

### `github_pages_encrypted_v1`

- owner: any registered module;
- route namespace: `/`;
- backend: `github_pages_encrypted_v1`;
- checked-out publication repository: `backend_options.repository_path` or `SKELETON_GITHUB_PAGES_REPO`;
- public base URL: `backend_options.base_url` or `SKELETON_GITHUB_PAGES_BASE_URL`;
- output: a public-safe loader plus authenticated encrypted payload.

The backend creates one self-contained HTML document by inlining local CSS, JavaScript, images and fonts. It derives independent encryption and MAC keys with HKDF-SHA256, encrypts with AES-256-CTR through OpenSSL and authenticates `version + salt + IV + ciphertext` with HMAC-SHA256. The browser loader verifies the MAC before decrypting and writing the page.

The fragment key is generated once, stored only in the private publisher state and reused for `update_owned`, so existing private URLs remain valid. Migration may seed an existing key through a mode-`0600` `backend_options.fragment_key_file` or an environment variable named by `fragment_key_env`; the secret value itself is never stored in a manifest.

`git_mode` may be:

- `none` — update a checked-out publication directory only;
- `commit` — stage and commit the owned route;
- `commit_push` — stage, commit and push, then perform HTTPS verification.

Failed verification restores the prior route. When a pushed revision fails verification, the backend creates and pushes a rollback commit when possible.

## Renderer contract

A renderer receives a validated manifest and an empty staging directory. It must create `index.html` and may add only profile-approved file types. Built-in templates are:

- `static_directory_v1` — copy a complete static directory;
- `single_html_v1` — copy one HTML file and optional asset directory.

Domain renderers are registered with:

```python
from core.page_renderer_registry import register_renderer
register_renderer("travel_trip_v1", render_travel_trip)
```

Renderers must not publish, mutate calendars, access backend credentials or print private content.

## Asset and privacy validation

The publisher rejects:

- missing `index.html` or title;
- symlinks, traversal, missing local assets and forbidden extensions;
- profile entry or uncompressed-size limit violations;
- private-key and common credential markers in rendered text;
- external images without HTTPS source metadata, author, licence, retrieval date and matching alt text.

The asset manifest is JSON or YAML:

```json
{
  "assets": [{
    "subject": "Duomo",
    "asset_url": "https://upload.wikimedia.org/...",
    "source_url": "https://commons.wikimedia.org/wiki/File:...",
    "author": "Author",
    "license": "CC BY-SA 4.0",
    "retrieval_date": "2026-07-20",
    "alt_text": "Фасад Міланського собору"
  }]
}
```

## Receipts and state

Private state defaults to:

```text
~/.local/share/skeleton/page-pipeline/
```

It contains:

- deterministic build ZIPs;
- route ownership and latest verified revision;
- private fragment keys where required;
- private receipts;
- rollback copies.

CLI output omits private URLs. A verified downstream action receives the private receipt in-process, allowing a Travel Calendar adapter to add the link only after successful publication. Downstream actions never run after failed verification.

Expected statuses:

- `BUILT`
- `PUBLISHED`
- `NO_CHANGE`
- `PUBLISHED_WITH_ACTION_ERRORS`
- `BLOCKED`

## Legacy maintenance tasks

The original controlled handoff remains available:

- `prepare_page_publication_handoff`
- `publish_static_page`

Historic aliases also remain for compatibility. They are not the normal path for creating new page instances.
