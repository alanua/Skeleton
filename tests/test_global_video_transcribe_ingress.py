from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from core import skeleton_media_bridge as media


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/video_transcribe_ingress.schema.json").read_text("utf-8"))
REGISTRY = yaml.safe_load((ROOT / "CAPABILITY_REGISTRY.yaml").read_text("utf-8"))
URL = "https://youtu.be/Synthetic123"


def _request(**overrides):
    return {"operation": "video_transcribe_url", "url": URL, **overrides}


def _allow(caller: str, operation: str) -> bool:
    return caller in {"ancillary-alpha", "ancillary-beta"} and operation == "video_transcribe_url"


def _planned(caller: str = "ancillary-alpha", **overrides):
    return media.plan_global_video_transcribe_ingress(
        _request(**overrides), authenticated_caller=caller, trusted_gate_check=_allow,
    )


def test_one_global_catalog_and_nine_semantic_actions_without_activation() -> None:
    catalog = media.global_video_provider_catalog()
    assert catalog["provider_id"] == "skeleton.video_understanding.global"
    assert catalog["status"] == "SOURCE_ONLY_NOT_DEPLOYED"
    assert catalog["runtime_registered"] is False
    assert catalog["live_runtime_execution"] is False
    assert catalog["control_mcp_tools_changed"] is False
    assert catalog["shared_worker_policy"] == "ONE_EXISTING_VIDEO_WORKER"
    assert catalog["shared_queue_policy"] == "ONE_EXISTING_VIDEO_QUEUE"
    assert len(catalog["operations"]) == 9
    assert len(set(catalog["operations"])) == 9
    assert "video_transcribe_url" in catalog["operations"]
    assert "video_understand_url" in catalog["operations"]
    assert catalog["required_external_source_pr"] == "alanua/skeleton-media#71"
    assert catalog["external_source_sha"] == media.SKELETON_MEDIA_REQUIRED_SHA

    registered = REGISTRY["capabilities"]["video_understanding_global"]
    assert registered["external_source_sha"] == catalog["external_source_sha"]
    assert registered["external_source_merge_state"] == "merged_source_only"
    assert registered["provider_count"] == 1
    assert registered["provider_id"] == catalog["provider_id"]
    assert registered["operation_ids"] == catalog["operations"]
    assert registered["status"] == "stage1_dry_run"
    assert registered["live_runtime_execution"] is False
    assert registered["runtime_registered"] is False
    assert registered["deployment_state"] == "NOT_DEPLOYED"
    assert registered["worker_policy"] == "one_existing_video_worker"
    assert registered["queue_policy"] == "one_existing_video_queue"


def test_schema_proves_no_user_controlled_gate_or_private_runtime_fields() -> None:
    assert SCHEMA["$schema"].endswith("/draft/2020-12/schema")
    assert SCHEMA["type"] == "object"
    assert SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == {"operation", "url"}
    props = SCHEMA["properties"]
    assert set(props) == {"operation", "url", "mode", "verify_with_independent_asr"}
    assert props["operation"]["const"] == "video_transcribe_url"
    assert props["mode"]["enum"] == ["QUICK", "STANDARD"]
    assert props["verify_with_independent_asr"]["type"] == "boolean"
    pattern = re.compile(props["url"]["pattern"])
    assert pattern.fullmatch(URL)
    assert pattern.fullmatch("https://www.youtube.com/watch?v=AbCdEf12345")
    for value in ("http://youtu.be/a", "https://evil.youtube.com/a", "https://127.0.0.1/a"):
        assert pattern.fullmatch(value) is None
    # Runtime preflight separately tests invalid modes, flags, fields and URLs,
    # never relying on optional JSON Schema libraries in the production host.


def test_cross_ancillary_is_same_provider_without_private_data_in_public_receipt() -> None:
    a = _planned("ancillary-alpha", mode="QUICK", verify_with_independent_asr=True)
    b = _planned("ancillary-beta", mode="QUICK", verify_with_independent_asr=True)
    for response in (a, b):
        private = response["private_plan"]
        public = response["public_receipt"]
        assert private["provider_id"] == media.GLOBAL_VIDEO_PROVIDER_ID
        assert private["url"] == URL
        assert private["mode"] == "QUICK"
        assert private["verify_with_independent_asr"] is True
        assert private["canonical_mutation"] == "NOT_USED"
        assert private["runtime_registered"] is False
        assert private["execution_requested"] is False
        assert public["provider_id"] == media.GLOBAL_VIDEO_PROVIDER_ID
        assert public["status"] == "PLANNED_NOT_REGISTERED"
        assert public["execution_performed"] is False
        for secret in (URL, "Synthetic123", "ancillary-alpha", "ancillary-beta", "audio_wav"):
            assert secret not in json.dumps(public)


@pytest.mark.parametrize("caller", [None, "", "unknown-actor", "../unauthorized", "evil actor", 4])
def test_missing_or_unauthorized_authenticated_caller_fails_closed(caller) -> None:
    with pytest.raises(media.GlobalVideoIngressError):
        media.plan_global_video_transcribe_ingress(
            _request(), authenticated_caller=caller, trusted_gate_check=_allow,
        )


def test_missing_denied_and_crashing_gate_never_builds_private_plan() -> None:
    with pytest.raises(media.GlobalVideoIngressError, match="VIDEO_INGRESS_GATE_UNAVAILABLE"):
        media.plan_global_video_transcribe_ingress(
            _request(), authenticated_caller="ancillary-alpha", trusted_gate_check=None,
        )
    with pytest.raises(media.GlobalVideoIngressError, match="VIDEO_INGRESS_GATE_DENIED"):
        media.plan_global_video_transcribe_ingress(
            _request(), authenticated_caller="ancillary-alpha", trusted_gate_check=lambda *_: False,
        )

    def broken_gate(*_args):
        raise RuntimeError("synthetic private gate failure")

    with pytest.raises(media.GlobalVideoIngressError, match="VIDEO_INGRESS_GATE_DENIED"):
        media.plan_global_video_transcribe_ingress(
            _request(), authenticated_caller="ancillary-alpha", trusted_gate_check=broken_gate,
        )


@pytest.mark.parametrize("key", (
    "operator_approval_ref", "authenticated_caller", "gate_decision", "secret",
    "cmd", "cookie", "headers", "output_path", "project_hint", "depth",
))
def test_arbitrary_operator_and_runtime_controls_are_rejected(key: str) -> None:
    with pytest.raises(media.GlobalVideoIngressError, match="VIDEO_INGRESS_FIELDS_FORBIDDEN"):
        media.plan_global_video_transcribe_ingress(
            _request(**{key: "user-controlled"}),
            authenticated_caller="ancillary-alpha", trusted_gate_check=_allow,
        )


@pytest.mark.parametrize("candidate", (
    "http://youtu.be/Synthetic123",
    "https://localhost/clip",
    "https://127.0.0.1/clip",
    "https://evil.youtube.com/clip",
    "https://youtu.be@evil.example/clip",
    "https://user:pass@youtu.be/clip",
    "https://youtu.be/",
    "https://youtu.be/clip#fragment",
    "https://youtu.be:8080/clip",
    " https://youtu.be/clip",
    "https://youtu.be/clip\nInjected",
    "file:///tmp/clip",
))
def test_untrusted_urls_are_rejected_without_fetch(candidate: str) -> None:
    with pytest.raises(media.GlobalVideoIngressError, match="VIDEO_INGRESS_URL_INVALID"):
        media.plan_global_video_transcribe_ingress(
            _request(url=candidate),
            authenticated_caller="ancillary-alpha", trusted_gate_check=_allow,
        )


def test_invalid_mode_and_asr_type_fail_closed() -> None:
    for mode in ("DEEP", "TARGETED", "ARCHIVE", 3, None):
        with pytest.raises(media.GlobalVideoIngressError, match="VIDEO_INGRESS_MODE_UNSUPPORTED"):
            _planned(mode=mode)
    for value in (None, 1, "yes", [], {}):
        with pytest.raises(media.GlobalVideoIngressError, match="VIDEO_INGRESS_ASR_FLAG_INVALID"):
            _planned(verify_with_independent_asr=value)


def test_existing_media_route_and_mcp_boundary_remain_unchanged() -> None:
    assert media.SKELETON_MEDIA_REPOSITORY == "alanua/skeleton-media"
    assert media.SKELETON_MEDIA_REQUIRED_SHA == "e4531a3aad4a830b9d3dd5b55c8eff7f444e6a5d"
    with pytest.raises(media.SkeletonMediaDependencyError, match="unsupported_skeleton_media_module"):
        media.dependency_source("video_understanding.operations")
    assert "video_understanding.operations" not in media._SUPPORTED_MODULES
