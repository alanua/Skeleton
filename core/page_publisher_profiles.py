from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PublicationProfile:
    profile_id: str
    owner_project: str
    namespace_prefix: str
    visibility: str
    backend: str
    page_id_pattern: str
    max_inline_ciphertext_bytes: int
    max_uncompressed_bytes: int
    max_entries: int
    allowed_extensions: frozenset[str]
    requires_encryption: bool = False
    supports_owned_updates: bool = True
    public_base_url_env: str | None = None
    publication_root_env: str | None = None

    def validate_page_id(self, page_id: str) -> bool:
        return re.fullmatch(self.page_id_pattern, page_id or "") is not None

    def url_path(self, page_id: str) -> str:
        if not self.validate_page_id(page_id):
            raise ValueError("invalid_page_id")
        prefix = self.namespace_prefix.rstrip("/")
        return f"{prefix}/{page_id}" if prefix else f"/{page_id}"


_BASE_EXTENSIONS = frozenset({
    ".html", ".css", ".js", ".json", ".webmanifest", ".png", ".jpg",
    ".jpeg", ".webp", ".svg", ".ico", ".txt", ".md", ".woff", ".woff2",
})

PROFILES: dict[str, PublicationProfile] = {
    "travel_private_v1": PublicationProfile(
        profile_id="travel_private_v1",
        owner_project="travel",
        namespace_prefix="/travel",
        visibility="tailnet_private",
        backend="tailscale_serve_static_v1",
        page_id_pattern=r"^[a-z0-9][a-z0-9-]{2,63}$",
        max_inline_ciphertext_bytes=42 * 1024,
        max_uncompressed_bytes=8 * 1024 * 1024,
        max_entries=160,
        allowed_extensions=_BASE_EXTENSIONS,
    ),
    "filesystem_static_v1": PublicationProfile(
        profile_id="filesystem_static_v1",
        owner_project="*",
        namespace_prefix="/pages",
        visibility="local",
        backend="filesystem_static_v1",
        page_id_pattern=r"^[a-z0-9][a-z0-9-]{2,63}$",
        max_inline_ciphertext_bytes=0,
        max_uncompressed_bytes=32 * 1024 * 1024,
        max_entries=500,
        allowed_extensions=_BASE_EXTENSIONS,
        publication_root_env="SKELETON_PAGE_FILESYSTEM_ROOT",
    ),
    "github_pages_encrypted_v1": PublicationProfile(
        profile_id="github_pages_encrypted_v1",
        owner_project="*",
        namespace_prefix="/",
        visibility="private_link",
        backend="github_pages_encrypted_v1",
        page_id_pattern=r"^[a-z0-9][a-z0-9-]{2,63}$",
        max_inline_ciphertext_bytes=0,
        max_uncompressed_bytes=16 * 1024 * 1024,
        max_entries=300,
        allowed_extensions=_BASE_EXTENSIONS,
        requires_encryption=True,
        public_base_url_env="SKELETON_GITHUB_PAGES_BASE_URL",
        publication_root_env="SKELETON_GITHUB_PAGES_REPO",
    ),
}


def get_profile(profile_id: str) -> PublicationProfile | None:
    return PROFILES.get(profile_id)


def validate_registry() -> None:
    prefixes: dict[tuple[str, str], str] = {}
    allowed_backends = {
        "tailscale_serve_static_v1",
        "filesystem_static_v1",
        "github_pages_encrypted_v1",
    }
    allowed_visibility = {"tailnet_private", "public", "local", "private_link"}
    for profile_id, profile in PROFILES.items():
        if profile.profile_id != profile_id:
            raise ValueError("profile_id_mismatch")
        if not profile.namespace_prefix.startswith("/"):
            raise ValueError("invalid_namespace_prefix")
        if profile.namespace_prefix != "/" and profile.namespace_prefix.endswith("/"):
            raise ValueError("invalid_namespace_prefix")
        key = (profile.backend, profile.namespace_prefix)
        previous = prefixes.setdefault(key, profile_id)
        if previous != profile_id:
            raise ValueError("duplicate_backend_namespace")
        if profile.visibility not in allowed_visibility:
            raise ValueError("invalid_visibility")
        if profile.backend not in allowed_backends:
            raise ValueError("unsupported_backend")
        if profile.max_uncompressed_bytes <= 0 or profile.max_entries <= 0:
            raise ValueError("invalid_profile_limits")


validate_registry()
