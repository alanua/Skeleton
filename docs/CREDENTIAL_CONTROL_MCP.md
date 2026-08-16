# Skeleton credential control boundary

The canonical runtime path is:

`service -> registered alias/action -> CredentialControlAdapter -> CredentialBroker -> SecretStoreGate -> provider -> registered target`

`adapters.credential_mcp.CredentialMcpAdapter` is the transport-neutral server-side MCP boundary. It receives an already service-bound `CredentialControlAdapter`; therefore the caller cannot select or spoof the service identity.

Exposed operations are exactly `credential_probe`, `credential_find`, and `credential_use`. Their input schemas do not contain provider commands, executable paths, environment-variable names, hosts, output destinations, or a secret-value field. `credential_use` can only execute the action already registered in the underlying `ServiceCredentialBinding`.

The current Runner/OpenHands OpenRouter credential is registered in `integrations.credential_runtime` and the Runner consumer imports that provider-neutral registered-credential surface. Bitwarden-specific token/reference handling remains behind the integration layer and the shared `CredentialBroker` path.

Repository-side callable contract is complete after this change. The ChatGPT registration descriptor is `adapters/chatgpt/CREDENTIAL_CONTROL_REGISTRATION.json`. The repository does not own registration of a new external ChatGPT connector in the product runtime, so the remaining external step after merge/runtime validation is exactly:

`CONNECTOR_REGISTRATION_REQUIRED`

Secret values must never appear in connector responses, GitHub receipts, logs, repr output, or caller-selected destinations.
