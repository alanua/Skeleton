# Repository inspection and patch scope

Inspect at minimum:

- `BOOT_MANIFEST.yaml`
- `OPERATOR_RULES.yaml`
- `MEMORY_ROUTING.yaml`
- `core/memory_bootstrap.py`
- `core/memory_scope_resolver.py`
- `core/memory_gateway.py`
- `core/memory_gateway_policy.py`
- `core/memory_gateway_storage.py`
- `core/private_memory_stack.py`
- `core/cognee_projection_adapter.py`
- `core/mempalace_adapter.py`
- `core/graphify_adapter.py`
- `core/semantic_memory_projection.py`
- `scripts/activate_five_layer_private_memory.py`
- `scripts/runner_poll_github_tasks.py`
- memory schemas, tests, and docs adjacent to those files
- issues #1904 and #1957 and their comments
- source PRs referenced by #1957

Preferred patch scope:

- the implementation, script, schema, test, and memory-doc files listed above;
- new narrowly scoped tests/docs when necessary.

Do not modify:

- `BOOT_MANIFEST.yaml`
- `PROJECT_TREE.yaml`
- `OPERATOR_RULES.yaml`
- `CAPABILITY_REGISTRY.yaml`
- workflows;
- Runner core or dispatch registration unless the audit proves a minimal change is unavoidable and review.md isolates it as protected;
- deployment/server configuration;
- secrets;
- private/runtime data;
- unrelated domains, especially #1958.

If a protected file change is necessary, provide it as a separate optional patch with justification. Do not mix it into the default patch.
