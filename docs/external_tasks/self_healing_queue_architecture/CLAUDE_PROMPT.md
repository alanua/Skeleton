# Claude review brief

You are the independent control-plane / distributed-systems reviewer for Skeleton's self-healing execution queue.

Start with `README.md`, then read `TASK.md`, `CURRENT_STATE.md`, `FAILURE_CASES.md`, `ARCHITECTURE_CONSTRAINTS.md`, `SOURCE_INDEX.md`, and `DELIVERABLE_CONTRACT.md` in this directory. Inspect the linked current repository code/issues where useful.

Your specific emphasis is formal control-plane correctness:

- one canonical durable task state machine and one per-attempt/route state machine;
- exactly-once ownership of state transitions;
- leases, heartbeats, transactions, idempotency and restart recovery;
- reconciliation after uncertain publication or side effects;
- elimination of split-brain authority between Scheduler, Runner, labels and executor wrappers;
- deterministic liveness: why eligible unrelated work continues without chat/manual nudges;
- exact evidence required before `DONE`;
- precise boundary between retry, reroute, recovery and `NEEDS_OPERATOR`;
- smallest safe migration from current main, including deletion of transitional compatibility paths when appropriate.

Be adversarial toward the current architecture. Do not preserve a mechanism merely because it already exists. Preserve the authority/privacy/protected-operation invariants in `ARCHITECTURE_CONSTRAINTS.md`.

Return one complete Markdown document using the section headings in `DELIVERABLE_CONTRACT.md` exactly. If you can write to a fork/branch, preferred result path is `results/CLAUDE.md`; otherwise return the complete Markdown to the operator.

Do not write production code, mutate runtime state, expose private runtime paths, or include secrets/private data.