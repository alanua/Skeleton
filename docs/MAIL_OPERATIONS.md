# Mail Operations

Mail Operations is a private local intake worker. The Scheduler owns cadence; the
worker is a oneshot target that polls configured provider accounts, writes
restart-safe idempotency state, registers extracted deadline checkpoints in the
existing Scheduler store, and prepares Telegram operator packets without sending
mail or mutating provider mailboxes.

Privacy boundary:

- Private intake: raw provider IDs, subjects, senders, previews, attachment refs,
  and credential refs remain local in the private mail state/provider boundary.
- Public receipts: aggregate counts, stable case/correspondence refs, and status
  only. Receipts set `private_payloads_included=false`.
- External send is disabled. Gmail cleanup is not executed by this worker.

Activation contract for a later live Gmail canary:

1. Install with `scripts/install_mail_operations_worker.sh` as root.
2. Create `/var/lib/skeleton/mail-operations/accounts.json` mode `0600`, owned by
   the service user, containing `skeleton.mail_provider.account.v1` account refs
   and opaque `secret_reference` pointers only.
3. Ensure the existing Scheduler database remains at
   `/var/lib/skeleton/scheduler/scheduler.sqlite3` or set
   `SKELETON_SCHEDULER_STATE_ROOT`.
4. Run the health canary first:
   `python3 /opt/skeleton-mail-operations/scripts/mail_operations_worker.py --health ...`
5. Enable/start `skeleton-mail-operations.timer` only after the private connector
   can resolve the secret reference. Missing auth returns bounded `AUTH_REQUIRED`.

Technical GitHub notifications may be cleanup candidates, but cleanup authority
requires exact GitHub authority correlation plus durable handoff headers. Even
then this worker only records aggregate authorization; it performs no destructive
provider mutation.
