from pathlib import Path
import pytest
from core.family_document_sources import ApprovedRoot, SourceError, inventory_sources, resolve_source, stable_observation

def test_inventory_and_resolve_are_bounded_to_approved_roots(tmp_path):
    root=tmp_path/"approved"; root.mkdir(); good=root/"scan.pdf"; good.write_bytes(b"pdf"); (root/"ignore.exe").write_bytes(b"x"); (root/"pending.pdf.part").write_bytes(b"x"); approved=(ApprovedRoot("mfp",root),); assert [i.relative_path for i in inventory_sources(approved)]==["scan.pdf"]; assert resolve_source(good,approved).absolute_path==good.resolve(); outside=tmp_path/"outside.pdf"; outside.write_bytes(b"x")
    with pytest.raises(SourceError) as exc: resolve_source(outside,approved)
    assert exc.value.reason_code=="source_outside_approved_roots"
def test_symlink_escape_is_rejected(tmp_path):
    root=tmp_path/"approved"; root.mkdir(); outside=tmp_path/"outside.pdf"; outside.write_bytes(b"x"); link=root/"link.pdf"; link.symlink_to(outside); approved=(ApprovedRoot("mfp",root),)
    with pytest.raises(SourceError) as exc: resolve_source(link,approved)
    assert exc.value.reason_code=="source_symlink_rejected" and inventory_sources(approved)==()
def test_settling_requires_same_size_and_mtime_for_interval(tmp_path):
    root=tmp_path/"approved"; root.mkdir(); source=root/"scan.pdf"; source.write_bytes(b"abc"); ref=resolve_source(source,(ApprovedRoot("mfp",root),)); stable,obs=stable_observation(ref,None,observed_at=10,settle_seconds=3); assert not stable; assert not stable_observation(ref,obs,observed_at=12,settle_seconds=3)[0]; assert stable_observation(ref,obs,observed_at=13,settle_seconds=3)[0]
def test_parent_directory_symlink_is_rejected(tmp_path):
    real=tmp_path/"real"; real.mkdir(); (real/"scan.pdf").write_bytes(b"pdf"); linked=tmp_path/"linked"; linked.symlink_to(real,target_is_directory=True)
    with pytest.raises(SourceError) as exc: ApprovedRoot("mfp",linked)
    assert exc.value.reason_code=="approved_root_symlinked"
