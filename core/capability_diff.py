from __future__ import annotations

from collections.abc import Iterable, Mapping

from core.capability_model import ImpactLevel, ObservedDiffImpact, classify_path_impact, max_impact


def observed_diff_impact(
    changed_files: Iterable[str],
    *,
    registry_metadata: Mapping[str, object] | None = None,
    invariant_metadata: Mapping[str, object] | None = None,
) -> ObservedDiffImpact:
    files = tuple(sorted({str(path) for path in changed_files if str(path)}))
    levels = [classify_path_impact(path) for path in files]
    reasons: set[str] = set()
    protected_files: list[str] = []
    mutating_files: list[str] = []
    production_files: list[str] = []
    for path, level in zip(files, levels, strict=True):
        if level is ImpactLevel.PROTECTED:
            protected_files.append(path)
            reasons.add("protected_file_changed")
        elif level is ImpactLevel.RED:
            mutating_files.append(path)
            production_files.append(path)
            reasons.add("production_or_mutating_file_changed")
        elif level is ImpactLevel.YELLOW:
            production_files.append(path)
            reasons.add("repository_code_changed")
    if registry_metadata:
        if bool(registry_metadata.get("protected_capability_changed")):
            levels.append(ImpactLevel.PROTECTED)
            reasons.add("registry_protected_capability_changed")
    if invariant_metadata:
        if bool(invariant_metadata.get("invariant_authority_changed")):
            levels.append(ImpactLevel.PROTECTED)
            reasons.add("invariant_authority_changed")
    return ObservedDiffImpact(
        level=max_impact(*levels),
        reasons=tuple(sorted(reasons)),
        changed_files=files,
        protected_files=tuple(protected_files),
        mutating_files=tuple(mutating_files),
        production_files=tuple(production_files),
    )
