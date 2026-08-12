from integrations.mail_scheduler import InMemoryMailScheduler, SchedulerStoreMailScheduler
from tests.test_mail_operations import _mail
from core.mail_operations import build_scheduler_deadline_checkpoint, normalize_correspondence


def test_in_memory_mail_scheduler_dedupes_schedule_id():
    normalized = normalize_correspondence(_mail())
    checkpoint = build_scheduler_deadline_checkpoint(normalized, _mail(), now=1786400010).to_mapping()
    scheduler = InMemoryMailScheduler()

    first = scheduler.register_deadline_checkpoint(checkpoint, now=1786400010)
    second = scheduler.register_deadline_checkpoint(checkpoint, now=1786400020)

    assert first.created is True
    assert second.created is False
    assert len(scheduler.checkpoints) == 1


def test_scheduler_store_mail_scheduler_registers_once(tmp_path):
    normalized = normalize_correspondence(_mail())
    checkpoint = build_scheduler_deadline_checkpoint(normalized, _mail(), now=1786400010).to_mapping()
    scheduler = SchedulerStoreMailScheduler(tmp_path / "scheduler.sqlite3")

    first = scheduler.register_deadline_checkpoint(checkpoint, now=1786400010)
    second = scheduler.register_deadline_checkpoint(checkpoint, now=1786400020)

    assert first.created is True
    assert second.created is False
