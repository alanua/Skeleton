from __future__ import annotations

from core import private_static_site_runtime as legacy


def test_legacy_task_ids_remain_narrow_aliases() -> None:
    assert legacy.PREPARE_TASK_ID == "prepare_private_static_site_handoff"
    assert legacy.DEPLOY_TASK_ID == "deploy_private_static_site"
    assert callable(legacy.prepare_private_static_site_handoff)
    assert callable(legacy.deploy_private_static_site)


def test_private_key_markers_are_not_weakened() -> None:
    assert "-----BEGIN PRIVATE KEY-----" in legacy.PRIVATE_KEY_MARKERS
    assert "-----BEGIN OPENSSH PRIVATE KEY-----" in legacy.PRIVATE_KEY_MARKERS
