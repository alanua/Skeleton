"""Compatibility aliases for the canonical Skeleton Page Publisher.

The Runner still imports the historic private-static-site task IDs. They remain
as narrow aliases only; all behavior is profile-driven by page_publisher_runtime.
"""
from __future__ import annotations

from core.page_publisher_runtime import (
    LEGACY_PREPARE_TASK_ID as PREPARE_TASK_ID,
    LEGACY_PUBLISH_TASK_ID as DEPLOY_TASK_ID,
    PRIVATE_KEY_MARKERS,
    prepare_page_publication_handoff,
    publish_static_page,
)


def prepare_private_static_site_handoff(body: str, **kwargs: object) -> str:
    return prepare_page_publication_handoff(body, force_legacy_task_id=True, **kwargs)


def deploy_private_static_site(body: str, **kwargs: object) -> str:
    return publish_static_page(body, force_legacy_task_id=True, **kwargs)
