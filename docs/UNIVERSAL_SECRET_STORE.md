# Universal Skeleton SecretStore

Status of this document: implementation contract. Runtime is not declared WORKING until the post-merge Bitwarden canary passes.

## Goal

Bitwarden Secrets Manager is the current production secret provider, but Skeleton services depend only on the provider-neutral SecretStore contract.

Canonical path:

`service -> ServiceCredentialBinding -> CredentialBroker -> SecretStoreGate -> SecretStore provider -> registered delivery adapter -> service`

A service never imports Bitwarden-specific code. It asks for a logical credential alias that is registered for that service. The binding fixes the secret reference, policy context, action, delivery adapter and target. The caller cannot choose a command, executable, path, host, environment variable or output destination at request time.

## Service onboarding

A new service supplies one non-secret `ServiceCredentialBinding` per required credential. The binding contains:

- `service_id`: stable Skeleton service identity;
- `alias`: logical credential name used by the service;
- `reference`: provider + opaque secret reference, never the value;
- `context`: exact machine identity, audience and task kind accepted by `SecretStoreGate`;
- `action_id`: the only operation allowed for this binding;
- `adapter_id` and `target_id`: pre-registered delivery boundary;
- `required`: required secrets fail closed; optional secrets report `DEGRADED`;
- `reload_mode`: `per_use` resolves the provider on every use so rotation is picked up without changing repository/config plaintext.

The catalog schema is `skeleton.service_credentials.v1` in `schemas/service_credential_binding.schema.json`.

## Delivery adapters

`InProcessCredentialAdapter` delivers the redacted `ResolvedSecret` object only to a pre-registered trusted callback. The callback result must be `None`; any returned value is treated as a contract violation and the public receipt is `BLOCKED`.

`ProcessCredentialAdapter` launches only a pre-registered absolute executable and injects the secret into the fixed environment variable configured with that target. Stdout/stderr are captured and discarded so a child that prints the credential cannot leak it through the broker receipt. The caller cannot supply argv, environment variable, cwd or executable.

Systemd services use the same process adapter contract through a fixed service target/wrapper. The Bitwarden machine token remains in the trusted authority process/systemd credential boundary; application services do not need Bitwarden-specific code.

## Public control surface

`adapters.credential_control.CredentialControlAdapter` exposes only:

- `credential_probe` / `credential_find`: availability and bounded reason/status;
- `credential_use`: execute the binding's registered action and return a receipt.

Responses contain service id, alias, opaque reference, action, adapter, status, reason class and a hash of that safe receipt. They never contain secret material.

There is intentionally no `get_secret`, generic shell, generic environment injection or arbitrary destination API.

## Failure policy

Required credential failures are `BLOCKED`. Optional credential absence/provider failure is `DEGRADED`. Provider exceptions are collapsed to stable reason classes such as `SECRET_MISSING`, `SECRET_REVOKED`, `SECRET_OUT_OF_SCOPE` and `SECRET_PROVIDER_UNAVAILABLE`; raw provider output is not propagated.

## Rotation

`reload_mode=per_use` means the broker resolves through `SecretStoreGate` on every invocation and does not cache plaintext. A Bitwarden rotation is therefore visible on the next bounded use without changing service code or storing a new plaintext copy in the repository.

## Current OpenHands binding

PR #2814 remains compatible while this generic layer is introduced. Its current protected Runner child binding is not silently rewritten by this non-protected slice. After this PR passes validation, a separate exact-head protected change may migrate that consumer onto the same `ServiceCredentialBinding` path. The generic broker must already pass against the existing Bitwarden provider before that protected migration.

## Runtime completion gate

Do not call Bitwarden integration WORKING until all of these are true:

1. this broker/catalog/control code is merged and full tests pass;
2. runtime is synced to the merge SHA;
3. a live generic `credential_probe` resolves an existing Bitwarden-backed binding without exposing the value;
4. a live generic `credential_use` succeeds for a registered synthetic/inert target;
5. an out-of-scope service/action is rejected;
6. existing OpenHands/OpenRouter Bitwarden canary remains PASS;
7. if ChatGPT connector registration is external to the repository, the server-side operation is complete and the only remaining status is `CONNECTOR_REGISTRATION_REQUIRED`.
