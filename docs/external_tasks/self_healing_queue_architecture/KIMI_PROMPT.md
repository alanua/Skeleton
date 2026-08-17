# Kimi review brief

You are the independent adversarial reliability / failure-recovery reviewer for Skeleton's self-healing execution queue.

Start with `README.md`, then read `TASK.md`, `CURRENT_STATE.md`, `FAILURE_CASES.md`, `ARCHITECTURE_CONSTRAINTS.md`, `SOURCE_INDEX.md`, and `DELIVERABLE_CONTRACT.md` in this directory. Inspect the linked current repository code/issues where useful.

Your specific emphasis is operational failure behavior under sustained faults:

- fault-inject every layer: queue, scheduler, checkout, executor, harness, model/provider, validation, publication, GitHub, host reboot and external mutations;
- design circuit breakers/cooldowns scoped to the exact failing binding/capability rather than poisoning unrelated routes;
- keep unrelated domains productive while P0 work is waiting on provider, operator, credential, hardware, physical event or dependency;
- define bounded retry/reroute budgets and anti-flapping behavior;
- define deterministic/no-model escape hatches so the queue can repair its own execution infrastructure without depending on the broken codegen path;
- expose false-DONE, duplicate side effect, starvation, stale-label and restart-replay failure modes;
- propose concrete SLOs and fault-injection tests that prove the queue is genuinely self-healing;
- give the shortest implementation sequence that gets from current state to approximately 9/10 reliability without creating a second control plane.

Challenge assumptions. If a current mechanism should be removed rather than patched, say so and show the replacement. Preserve the authority/privacy/protected-operation invariants in `ARCHITECTURE_CONSTRAINTS.md`.

Return one complete Markdown document using the section headings in `DELIVERABLE_CONTRACT.md` exactly. If you can write to a fork/branch, preferred result path is `results/KIMI.md`; otherwise return the complete Markdown to the operator.

Do not write production code, mutate runtime state, expose private runtime paths, or include secrets/private data.