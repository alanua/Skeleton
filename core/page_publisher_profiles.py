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

    def validate_page_id(self, page_id: str) -> bool:
        return re.fullmatch(self.page_id_pattern, page_id or "") is not None

    def url_path(self, page_id: str) -> str:
        if not self.validate_page_id(page_id):
            raise ValueError("invalid_page_id")
        return f"{self.namespace_prefix.rstrip('/')}/{page_id}"


_BASE_EXTENSIONS = frozenset({
    ".html", ".css", ".js", ".json", ".webmanifest", ".png", ".jpg",
    ".jpeg", ".webp", ".svg", ".ico", ".txt", ".md",
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
}


def get_profile(profile_id: str) -> PublicationProfile | None:
    return PROFILES.get(profile_id)


def validate_registry() -> None:
    prefixes: dict[str, str] = {}
    for profile_id, profile in PROFILES.items():
        if profile.profile_id != profile_id:
            raise ValueError("profile_id_mismatch")
        if not profile.namespace_prefix.startswith("/") or profile.namespace_prefix.endswith("/"):
            raise ValueError("invalid_namespace_prefix")
        previous = prefixes.setdefault(profile.namespace_prefix, profile_id)
        if previous != profile_id:
            raise ValueError("duplicate_namespace_prefix")
        if profile.visibility not in {"tailnet_private", "public"}:
            raise ValueError("invalid_visibility")
        if profile.backend != "tailscale_serve_static_v1":
            raise ValueError("unsupported_backend")


validate_registry()
