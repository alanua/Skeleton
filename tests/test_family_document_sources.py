from __future__ import annotations

import json

import pytest

from core.family_document_sources import discover_source_artifacts, load_source_profiles, source_profile_from_mapping


def test_source_profiles_default_to_sixty_second_inactivity_window(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "schema": "skeleton.family_document_runtime_config.v1",
        "mfp_sources": [{"source_id": "mfp-a", "profile_id": "duplex"}],
    }), encoding="utf-8")

    profile = load_source_profiles(config)[0]

    assert profile.inactivity_window_seconds == 60
    assert profile.identity == "mfp-a:duplex"


def test_invalid_source_tokens_fail_closed():
    with pytest.raises(ValueError, match="invalid_source_id"):
        source_profile_from_mapping({"source_id": "../mfp", "profile_id": "duplex"})


def test_discovery_sorts_supported_artifacts_by_arrival_time(tmp_path):
    one = tmp_path / "one.pdf"
    two = tmp_path / "two.jpg"
    ignored = tmp_path / "ignore.txt"
    one.write_bytes(b"1")
    two.write_bytes(b"2")
    ignored.write_text("x", encoding="utf-8")
    one.touch()
    two.touch()
    profile = source_profile_from_mapping({"source_id": "mfp-a", "profile_id": "duplex", "intake_dir": str(tmp_path)})

    assert discover_source_artifacts(profile) == [one, two]
