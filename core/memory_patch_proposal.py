from core.memory_patch_common import (
    PATCH_PROPOSAL_EVENT_SCHEMA,
    PATCH_PROPOSAL_SCHEMA,
    REQUIRED_FIELDS,
    MemoryPatchProposalError,
    MemoryPatchProposalIdempotencyError,
    MemoryPatchProposalValidationError,
    PatchProposalResult,
    canonical_dedupe_key,
    canonical_idempotency_key,
    canonical_json,
    stable_hash,
)
from core.memory_patch_registry import MemoryPatchProposalRegistry

REQUIRED_PATCHPROPOSAL_FIELDS = REQUIRED_FIELDS

__all__ = [
    "PATCH_PROPOSAL_EVENT_SCHEMA",
    "PATCH_PROPOSAL_SCHEMA",
    "REQUIRED_PATCHPROPOSAL_FIELDS",
    "MemoryPatchProposalError",
    "MemoryPatchProposalIdempotencyError",
    "MemoryPatchProposalRegistry",
    "MemoryPatchProposalValidationError",
    "PatchProposalResult",
    "canonical_dedupe_key",
    "canonical_idempotency_key",
    "canonical_json",
    "stable_hash",
]
