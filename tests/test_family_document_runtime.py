from __future__ import annotations

from pathlib import Path
import pytest
from core.family_document_runtime import DurableJournal, FamilyDocumentWorker, ProjectionOutbox, RuntimeErrorCode, RuntimeLimits
from core.family_document_sources import ApprovedRoot

class Clock:
    def __init__(self): self.value = 1000.0
    def __call__(self): return self.value
    def advance(self, seconds): self.value += seconds
class Processor:
    def __init__(self, result=None, error=None): self.result = result or {"status":"DONE","reason_code":"projection_pending","counts":{"written":1}}; self.error=error; self.calls=[]
    def process(self, source: Path, *, dry_run=False):
        del dry_run; self.calls.append(source)
        if self.error: raise self.error
        return self.result
def prepared(tmp_path, *, clock, attempts=3):
    root=tmp_path/"inbox"; root.mkdir(); source=root/"scan.pdf"; source.write_bytes(b"pdf"); journal=DurableJournal(tmp_path/"journal.json", RuntimeLimits(settle_seconds=2, lease_seconds=30, max_attempts=attempts, retry_base_seconds=5), clock=clock); return root,source,journal,(ApprovedRoot("mfp",root),)

def test_worker_settles_claims_and_completes_without_stale_lock(tmp_path):
    clock=Clock(); _,source,journal,roots=prepared(tmp_path,clock=clock); processor=Processor(); worker=FamilyDocumentWorker(roots=roots,journal=journal,processor=processor,lock_path=tmp_path/"worker.lock"); assert worker.run_once()["operation"]=="IDLE"; clock.advance(2); result=worker.run_once(); assert result["queue_counts"]["DONE"]==1 and processor.calls==[source.resolve()]; assert worker.run_once()["operation"]=="IDLE"
def test_expired_processing_lease_recovers_after_crash(tmp_path):
    clock=Clock(); _,_,journal,roots=prepared(tmp_path,clock=clock); journal.discover(roots); journal.settle(roots); clock.advance(2); journal.settle(roots); assert journal.claim("worker-a"); clock.advance(31); assert journal.recover_expired()==1; item=next(iter(journal.store.snapshot()["items"].values())); assert item["state"]=="RETRY" and item["reason_code"]=="lease_expired"
def test_retry_backoff_and_quarantine_are_durable(tmp_path):
    clock=Clock(); _,_,journal,roots=prepared(tmp_path,clock=clock,attempts=2); journal.discover(roots); journal.settle(roots); clock.advance(2); journal.settle(roots); key,_=journal.claim("worker-a"); assert journal.fail(key,"worker-a","processing_failed")=="RETRY"; clock.advance(5); journal.settle(roots); key,_=journal.claim("worker-a"); assert journal.fail(key,"worker-a","processing_failed")=="QUARANTINED"; assert journal.health()["status"]=="BLOCKED"
def test_single_instance_lock_blocks_parallel_worker(tmp_path):
    clock=Clock(); _,_,journal,roots=prepared(tmp_path,clock=clock); first=FamilyDocumentWorker(roots=roots,journal=journal,processor=Processor(),lock_path=tmp_path/"worker.lock"); second=FamilyDocumentWorker(roots=roots,journal=journal,processor=Processor(),lock_path=tmp_path/"worker.lock")
    with first.single_instance():
        with pytest.raises(RuntimeErrorCode):
            with second.single_instance(): pass
def test_projection_outbox_retries_recovers_and_quarantines(tmp_path):
    clock=Clock(); outbox=ProjectionOutbox(tmp_path/"outbox.json",clock=clock); outbox.enqueue("fact:projection","a"*64); assert outbox.process_one(lambda k,d:False,max_attempts=2)=="RETRY"; clock.advance(30); assert outbox.process_one(lambda k,d:False,max_attempts=2)=="QUARANTINED"; outbox.enqueue("other:projection","b"*64); assert outbox.process_one(lambda k,d:True)=="DONE"
def test_transient_blocked_result_retries_instead_of_immediate_quarantine(tmp_path):
    clock=Clock(); _,_,journal,roots=prepared(tmp_path,clock=clock,attempts=3); worker=FamilyDocumentWorker(roots=roots,journal=journal,processor=Processor(result={"status":"BLOCKED","reason_code":"memory_exact_read_failed","counts":{}}),lock_path=tmp_path/"worker.lock"); worker.run_once(); clock.advance(2); result=worker.run_once(); assert result["queue_counts"]["RETRY"]==1 and result["status"]=="DEGRADED"
