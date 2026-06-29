"""Home-edge universal gateway contracts and remote diagnostic helpers."""

from .diagnostics import HomeEdgeDiagnosticError, build_operator_report, run_home_edge_diagnostic
from .gateway import GatewayActionSpec, gateway_contract, prepared_runtime_bootstrap
from .profile import HomeEdgeProfile, load_home_edge_profile
from .remote import HomeEdgeRemoteError, run_audited_home_edge_command
from .transport import HomeEdgeTransportError, OpenSSHTransport, TailscaleSSHTransport

__all__ = [
    "GatewayActionSpec",
    "HomeEdgeDiagnosticError",
    "HomeEdgeProfile",
    "HomeEdgeRemoteError",
    "HomeEdgeTransportError",
    "OpenSSHTransport",
    "TailscaleSSHTransport",
    "build_operator_report",
    "gateway_contract",
    "load_home_edge_profile",
    "prepared_runtime_bootstrap",
    "run_audited_home_edge_command",
    "run_home_edge_diagnostic",
]
