# Brother MFC-J4540DW WBM boundary (inactive)

The registered credential is `brother-mfc-j4540dw / wbm-admin`, action
`read-authenticated-status`. Its exact context is `home-edge-01`, audience
`brother-mfc-j4540dw-wbm`, task kind `bounded_maintenance_status`. The registered
systemd reference index must map this binding to the existing opaque reference.
No reference value was supplied with this task; none is invented or copied into
this repository. Index enrollment/mapping is a separate runtime gate.

Resolution uses the existing registered-credential runtime, CredentialBroker,
SecretStoreGate and BwsCliSecretsManagerStore authority. There is no new BWS
access-token path or Home Edge vault CLI. The consumer receives material only
inside the bounded in-process session; it must not persist, trace, screenshot,
log, export or place material in process arguments, stdin or environment.

## Vendor evidence and implementation limit

Brother's model-specific [login instructions](https://support.brother.com/g/s/id/htmldoc/mfc/cv_mfc4340dw/uke/html/GUID-AD0ABBB1-8AAF-4C51-ACF1-1D6DAC9EE8A6_1.html)
document HTTPS, entering the password, and clicking Login. The model-specific
[WBM access instructions](https://support.brother.com/g/s/id/htmldoc/mfc/cv_mfc4340dw/uke/html/GUID-01123D26-62DC-4557-9436-E6056568F2F8_1.html)
requires cookies and JavaScript and warns about login lockout. These sources
were reviewed on 2026-10-25. They do not establish HTTP form names, authentication
endpoints, a firmware-specific authenticated status selector, or a cleaning POST
contract. The access instructions also document Logout, supporting its use as
positive session evidence. No such fields or endpoints have been inferred.

`integrations.brother_wbm` therefore provides an internal, offline-tested session
port, not a working browser/HTTP adapter or an activated executor operation.
Its positive evidence contract requires the exact model, a Logout control, no
login form, HTTP 200 and no redirects on the enrolled HTTPS origin. A production
adapter must establish these facts from the rendered vendor UI; returning HTTP
200 alone is insufficient. Tests exercise synthetic evidence, not device firmware.

## Trust binding

Before a session can resolve a password, an independently enrolled binding must
associate the Device Registry Brother identity with its exact DNS name, trusted
CA file and SHA-256 leaf certificate fingerprint. The SSL context requires chain
validation and hostname verification, TLS 1.2 or newer; the explicit fingerprint
also blocks a changed certificate even if signed by the trusted CA. No certificate
is learned from a first connection. Unknown identity, absent CA, failed hostname
verification or changed certificate must block before credential delivery.
The DNS name must be a literal canonical lower-case hostname, without URL syntax,
credentials, port overrides or whitespace. A certificate rotation requires a new
independent enrollment; even a replacement with a valid chain remains blocked.
The future adapter must use this context and return the peer DER certificate
from that same verified connection, before authentication. It must prohibit
redirects, retries, cross-origin subrequests and downloads, bound execution time,
and close the ephemeral session on every outcome. The protocol alone cannot
enforce transport behavior; a reviewed concrete adapter is still required.

## Remaining gates

Do not activate this module until a reviewed session adapter and fixed read-only
worker are registered through `/usr/local/bin/home_edge_exec --server` with the
canonical signed Home Edge read-only request and receipt flow. No alternate SSH,
CLI or direct caller network path is introduced here. Review the deployed
firmware's UI evidence without credentials before implementing selectors.

Trust enrollment, index mapping verification, live secret resolution and runtime
activation remain separate gated actions; none was performed. No live HTTP,
printing, test-page generation or cleaning occurred. The scan/document conveyor
is untouched.

Black-only Normal cleaning is deliberately unregistered. It requires vendor
evidence of the exact POST contract and a verified Skeleton approval receipt
scoped to device, action, task and validity interval before any implementation
or execution. Chat text and arbitrary approval strings are not receipts.

Publication is delegated to Runner. This workspace did not inspect or extend
Draft PR #4829 and did not open a follow-up PR, in accordance with the no-gh,
no-commit, no-push publication contract.
