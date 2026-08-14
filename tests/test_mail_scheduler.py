from core.mail_operations import build_scheduler_deadline_checkpoint, normalize_correspondence
from core.scheduler_store import SchedulerStore
from integrations.mail_scheduler import register_mail_deadline_checkpoint


def test_mail_scheduler_replay_registers_exactly_once(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()
    envelope = {
        "provider": "gmail",
        "provider_message_ref": "mailmsg:abc",
        "received_at": 1786400000,
        "subject_hint": "Important deadline",
        "body_preview": "Deadline 2026-09-01",
        "importance_hint": "important",
    }
    normalized = normalize_correspondence(envelope)
    checkpoint = build_scheduler_deadline_checkpoint(normalized, envelope, now=1786400010)

    assert register_mail_deadline_checkpoint(store, checkpoint, message_hash=normalized.message_hash, now=1)
    assert not register_mail_deadline_checkpoint(store, checkpoint, message_hash=normalized.message_hash, now=2)
    assert store.get_current(checkpoint.schedule_id) is not None
