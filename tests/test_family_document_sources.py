from __future__ import annotations

from pathlib import Path

import pytest

from core.family_document_sources import ApprovedRoot, SourceError, inventory_sources, resolve_source, stable_observation


def test_inventory_and_resolve_are_bounded_to_approved_roots(tmp_path: Path) -> None:
    root = tmp_path / "approved"; root.mkdir()
    good = root / "scan.pdf"; good.write_bytes(b"pdf")
    (root / "ignore.exe").write_bytes(b"x")
    (root / "pending.pdf.part").write_bytes(b"x")
    approved = (ApprovedRoot("mfp", root),)
    assert [item.relative_path for item in inventory_sources(approved)] == ["scan.pdf"]
    assert resolve_source(good, approved).absolute_path == good.resolve()
    outside = tmp_path / "outside.pdf"; outside.write_bytes(b"x")
    with pytest.raises(SourceError) as exc:
        resolve_source(outside, approved)
    assert exc.value.reason_code == "source_outside_approved_roots"


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "approved"; root.mkdir()
    outside = tmp_path / "outside.pdf"; outside.write_bytes(b"x")
    link = root / "link.pdf"; link.symlink_to(outside)
    approved = (ApprovedRoot("mfp", root),)
    with pytest.raises(SourceError) as exc:
        resolve_source(link, approved)
    assert exc.value.reason_code == "source_symlink_rejected"
    assert inventory_sources(approved) == ()


def test_settling_requires_same_size_and_mtime_for_interval(tmp_path: Path) -> None:
    root = tmp_path / "approved"; root.mkdir()
    source = root / "scan.pdf"; source.write_bytes(b"abc")
    reference = resolve_source(source, (ApprovedRoot("mfp", root),))
    stable, observation = stable_observation(reference, None, observed_at=10.0, settle_seconds=3.0)
    assert stable is False
    assert stable_observation(reference, observation, observed_at=12.0, settle_seconds=3.0)[0] is False
    assert stable_observation(reference, observation, observed_at=13.0, settle_seconds=3.0)[0] is True
