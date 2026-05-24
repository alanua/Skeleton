# Operator Commands

## `зафіксуй`

`зафіксуй` means persist an instruction or rule change through Skeleton routing. It does not mean ChatGPT memory, session memory, or an informal promise that the assistant will remember something later.

The assistant must not claim persistence unless it created or updated a durable target, or created a task/PR for that durable target. Public-safe durable changes go through the Skeleton write gate, including the BZ/GitHub route when the target is canonical repository state. Private context stays on a private route. Secrets must never be written to chat, GitHub, or plain Drive.

Instruction changes are fixed in the durable source that owns the behavior:

- `COMMANDS.yaml` and `MODES.yaml` define command and mode behavior.
- `MEMORY_ROUTING.yaml` defines route behavior.
- `SOURCE_REGISTRY.yaml` defines trust and source behavior.
- `docs/` defines operator-facing rules.

Before merge, say `created task/PR to persist`. After merge, say `fixed in <file> via PR/commit`.

## Batch Processing

Batch processing is allowed for repeated same-type safe steps after approval when each item has the same approved route, scope, risk level, and gate. It never bypasses approval gates.

Batch processing must stop and split out any item that differs in type, scope, route, risk, or gate. Batch merge, deploy, secrets, runtime, canon, and instruction-promotion work requires explicit approval before it can be processed as a batch.
