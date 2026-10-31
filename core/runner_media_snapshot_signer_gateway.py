"""Synthetic review helper for #4924; deliberately NOT a root executor.

Activation dependency: separately review/authorize the protected gateway bootstrap,
its installed import closure, hardening action/registry pins, capability metadata,
and request schema together. The current bootstrap does not ship this module and
both gateway layers enforce four actions. Do not register this action piecemeal.

A future action must stage verified Git objects from the independently pinned media
repository (never request.checkout_path), execute only PROTECTED_INSTALLER as
root:root 0555, and audit installed blobs, ownership, modes and exact sudoers before
commit, retaining verified backups and rollback evidence. The pinned installer has
backup/rollback and staging verification but lacks that final installed-file audit.
This helper neither attests live provenance nor authorizes execution, even when all
synthetic evidence matches. No paths, commands, secrets, or IO callbacks are inputs.
"""
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from typing import Final

ACTION_ID: Final = "home_edge_media_snapshot_signer_reconcile_v1"
OPERATOR_APPROVAL: Final = "alanua/Skeleton#4922:" + ACTION_ID
SKELETON_MAIN_SHA: Final = "3d347a7e54639b86c34c52eecb23fb83fd021871"
SOURCE_REPOSITORY: Final = "alanua/skeleton-media"
SOURCE_MAIN_SHA: Final = "475df11fa38f910cc4f81a4dfbf535d04b7ad092"
PROTECTED_INSTALLER: Final = (
    "/usr/local/libexec/skeleton/home-edge/media-source-snapshot-installer/"
    "install_home_edge_media_source_snapshot_signer.sh"
)
# Git tree modes, independently read at SOURCE_MAIN_SHA; not filesystem modes.
ARTIFACTS: Final = (
    ("scripts/install_home_edge_media_source_snapshot_signer.sh", "100755",
     "b26493da46686a96d0edc5c79efb6ecba6a0045c"),
    ("scripts/home_edge_media_source_snapshot_signer_payload.py", "100644",
     "71bd0a237f4180fc52694a86c596fadf0b2ed687"),
    ("scripts/home_edge_media_source_snapshot_signer", "100644",
     "24620d9e9fe4f62c055113e6aeefb2d0984be2d5"),
    ("skeleton_media/home_edge/media_source_snapshot.py", "100644",
     "3e0a27bdb229db594813c2ea7bd8af035625e565"),
)
MAX_ARTIFACT_BYTES: Final = 256 * 1024


@dataclass(frozen=True)
class SyntheticArtifact:
    """In-memory evidence only; no checkout is opened or trusted."""

    tree_mode: str
    content: bytes
    symlink: bool = False


def validate_synthetic_action(
    *,
    action_id: str,
    operator_approval: str,
    skeleton_main_sha: str,
    source_repository: str,
    source_main_sha: str,
    source_clean: bool,
    artifacts: Mapping[str, SyntheticArtifact],
) -> dict[str, object]:
    """Validate fixed review evidence and ALWAYS fail closed on activation.

    Caller-supplied SHA/clean assertions are synthetic, not independent live trust.
    No success receipt or execution/activation switch exists in this API.
    """
    reason = "gateway_bootstrap_required"
    if action_id != ACTION_ID:
        reason = "action_not_registered"
    elif operator_approval != OPERATOR_APPROVAL:
        reason = "operator_approval_mismatch"
    elif skeleton_main_sha != SKELETON_MAIN_SHA:
        reason = "skeleton_main_sha_mismatch"
    elif source_repository != SOURCE_REPOSITORY:
        reason = "source_repository_mismatch"
    elif source_main_sha != SOURCE_MAIN_SHA:
        reason = "source_main_sha_mismatch"
    elif source_clean is not True:
        reason = "source_not_clean"
    elif set(artifacts) != {path for path, _, _ in ARTIFACTS}:
        reason = "source_artifact_set_mismatch"
    else:
        for path, mode, blob in ARTIFACTS:
            artifact = artifacts[path]
            if (
                not isinstance(artifact, SyntheticArtifact)
                or artifact.symlink is not False
                or artifact.tree_mode != mode
            ):
                reason = "source_type_or_mode_mismatch"
                break
            content = artifact.content
            if not isinstance(content, bytes) or not 0 < len(content) <= MAX_ARTIFACT_BYTES:
                reason = "source_size_invalid"
                break
            header = f"blob {len(content)}\0".encode("ascii")
            if hashlib.sha1(header + content).hexdigest() != blob:
                reason = "source_blob_mismatch"
                break
    return {
        "action_id": ACTION_ID,
        "status": "NEEDS_OPERATOR",
        "reason": reason,
        "activation_blocker": "gateway_bootstrap_required",
        "synthetic_only": True,
        "registered": False,
        "mutation_started": False,
        "mutation_performed": False,
        "external_side_effects_executed": False,
    }
