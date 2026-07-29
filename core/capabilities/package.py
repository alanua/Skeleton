from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.resolver_registry.models import ResolverCapabilityManifest


@dataclass(frozen=True)
class PackageVerification:
    ok: bool
    package_hash: str
    manifest_hash: str | None
    errors: tuple[str, ...] = ()


class CapabilityPackage:
    """Immutable package verifier for resolver capability artifacts."""

    REQUIRED_MEMBERS = frozenset(
        {
            "manifest.json",
            "dependency-lock.json",
            "fixtures/",
            "tests/",
            "code/",
            "operations/deploy.json",
            "operations/verify.json",
            "operations/rollback.json",
            "attestation.json",
        }
    )

    def __init__(self, artifact_path: Path) -> None:
        self.artifact_path = Path(artifact_path)

    def sha256(self) -> str:
        digest = hashlib.sha256()
        with self.artifact_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def verify(self, *, expected_hash: str | None = None) -> PackageVerification:
        errors: list[str] = []
        package_hash = self.sha256()
        if expected_hash and package_hash != expected_hash:
            errors.append("package hash mismatch")
        try:
            members = self._members()
            manifest_data = self._json_member("manifest.json")
            manifest = ResolverCapabilityManifest.from_mapping(manifest_data)
            manifest_hash = hashlib.sha256(manifest.canonical_json().encode()).hexdigest()
            attestation = self._json_member("attestation.json")
            if attestation.get("package_sha256") not in {None, package_hash, "external"}:
                errors.append("attestation package hash mismatch")
            if attestation.get("manifest_sha256") != manifest_hash:
                errors.append("attestation manifest hash mismatch")
            if not attestation.get("signature"):
                errors.append("signature metadata is required")
            for prefix in self.REQUIRED_MEMBERS:
                if prefix.endswith("/"):
                    if not any(member.startswith(prefix) and member != prefix for member in members):
                        errors.append(f"missing package directory: {prefix}")
                elif prefix not in members:
                    errors.append(f"missing package member: {prefix}")
        except Exception as exc:  # noqa: BLE001 - verifier returns a bounded failure object.
            manifest_hash = None
            errors.append(str(exc))
        return PackageVerification(not errors, package_hash, manifest_hash, tuple(errors))

    def manifest(self) -> ResolverCapabilityManifest:
        return ResolverCapabilityManifest.from_mapping(self._json_member("manifest.json"))

    def _members(self) -> set[str]:
        with tarfile.open(self.artifact_path, "r:gz") as archive:
            names = set()
            for member in archive.getmembers():
                name = member.name.lstrip("./")
                if name.startswith("/") or ".." in Path(name).parts:
                    raise ValueError("package contains unsafe member path")
                names.add(name)
            return names

    def _json_member(self, name: str) -> Mapping[str, Any]:
        with tarfile.open(self.artifact_path, "r:gz") as archive:
            handle = archive.extractfile(name)
            if handle is None:
                raise ValueError(f"missing package member: {name}")
            value = json.loads(handle.read().decode("utf-8"))
            if not isinstance(value, Mapping):
                raise ValueError(f"{name} must contain a JSON object")
            return value
