# Execution and safety contract

Allowed:

- public repository source;
- synthetic fixtures;
- local disposable test environment;
- proposal code and patches;
- deterministic tests;
- public-safe aggregate reports.

Forbidden:

- private values, records, paths, datasets, names, addresses, OCR, or canonical references;
- secrets or credentials;
- direct writes to a real canonical database;
- reading or modifying the legacy connector database;
- production deployment or service activation;
- merge or self-approval;
- force push, destructive migration, silent overwrite, or unbounded filesystem scanning;
- cloud LLM/embedding endpoints, telemetry, or provider fallback;
- creating another canonical database;
- treating model output as approved canon;
- modifying #1958 or using general memory as the financial authority.

Dependency/runtime requirements:

- install into an isolated environment; do not modify system Python;
- pin exact versions and hashes where practical;
- local Ollama detection must distinguish: binary missing, service unavailable, endpoint unavailable, model absent, incompatible model, and resource failure;
- installation may prepare service definitions but must not silently enable/start production services;
- all commands must be bounded and idempotent;
- public receipts expose only stable reason codes, versions, booleans, counts, and hashes.
