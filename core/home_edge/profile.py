from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = Path("config/home_edge/home-edge-01.json")
LOCAL_PROFILE_ENV = "SKELETON_HOME_EDGE_01_PROFILE"
ENV_OVERRIDES = {
    "hostname": "SKELETON_HOME_EDGE_01_HOSTNAME",
    "tailscale_ip": "SKELETON_HOME_EDGE_01_TAILSCALE_IP",
    "controller_host": "SKELETON_HOME_EDGE_01_CONTROLLER_HOST",
    "controller_tailscale_ip": "SKELETON_HOME_EDGE_01_CONTROLLER_TAILSCALE_IP",
    "target_user": "SKELETON_HOME_EDGE_01_TARGET_USER",
}


@dataclass(frozen=True)
class PublicProfileFingerprint:
    hostname: str
    tailscale_ip: str
    controller_host: str
    controller_tailscale_ip: str
    target_user: str

    @property
    def private_values(self) -> frozenset[str]:
        return frozenset(
            value
            for value in (
                self.hostname,
                self.tailscale_ip,
                self.controller_host,
                self.controller_tailscale_ip,
                self.target_user,
            )
            if value
        )


HOME_EDGE_PUBLIC_PROFILE = PublicProfileFingerprint(
    hostname="synthetic-home-edge",
    tailscale_ip="100.64.0.10",
    controller_host="synthetic-controller",
    controller_tailscale_ip="100.64.0.11",
    target_user="home-edge-runner",
)


@dataclass(frozen=True)
class HomeEdgeProfile:
    node_id: str
    hostname: str
    tailscale_ip: str
    controller_host: str
    controller_tailscale_ip: str
    target_user: str
    transport: str
    identity_env: str
    known_hosts_env: str
    task_model: str
    risk_lanes: tuple[str, ...]
    os: str
    primary_network: dict[str, Any]
    capabilities: tuple[str, ...]
    safety_boundaries: tuple[str, ...]
    source: str = "synthetic_template"

    @classmethod
    def from_mapping(
        cls, data: dict[str, Any], *, source: str = "local_profile"
    ) -> "HomeEdgeProfile":
        ssh = _mapping(data, "ssh")
        controller = _mapping(data, "controller")
        gateway = _mapping(data, "gateway")
        primary_network = _mapping(data, "primary_network")
        profile = cls(
            node_id=_string(data, "node_id"),
            hostname=_string(data, "hostname"),
            tailscale_ip=_string(data, "tailscale_ip"),
            controller_host=_string(controller, "host"),
            controller_tailscale_ip=_string(controller, "tailscale_ip"),
            target_user=_string(ssh, "target_user"),
            transport=_string(ssh, "transport"),
            identity_env=_string(ssh, "identity_env"),
            known_hosts_env=_string(ssh, "known_hosts_env"),
            task_model=_string(gateway, "task_model"),
            risk_lanes=tuple(_strings(gateway, "risk_lanes")),
            os=_string(data, "os"),
            primary_network=dict(primary_network),
            capabilities=tuple(_strings(data, "capabilities")),
            safety_boundaries=tuple(_strings(data, "safety_boundaries")),
            source=source,
        )
        profile.validate_fixed_runner_contract()
        return profile

    @property
    def is_template_identity(self) -> bool:
        return (
            self.hostname == HOME_EDGE_PUBLIC_PROFILE.hostname
            and self.tailscale_ip == HOME_EDGE_PUBLIC_PROFILE.tailscale_ip
            and self.controller_host == HOME_EDGE_PUBLIC_PROFILE.controller_host
            and self.controller_tailscale_ip
            == HOME_EDGE_PUBLIC_PROFILE.controller_tailscale_ip
            and self.target_user == HOME_EDGE_PUBLIC_PROFILE.target_user
        )

    def validate_fixed_runner_contract(self) -> None:
        expected = {
            "node_id": "home-edge-01",
            "transport": "openssh_over_tailscale_ip",
            "identity_env": "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE",
            "known_hosts_env": "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE",
            "task_model": "typed_allowlisted_actions",
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError(f"home-edge fixed contract mismatch for {field}")


def load_home_edge_profile(path: str | Path | None = None) -> HomeEdgeProfile:
    profile_path = _profile_path(path)
    if profile_path is None:
        data = synthetic_profile_mapping()
        source = "synthetic_template"
    else:
        data = _read_profile_mapping(profile_path)
        source = "local_profile"
    data = _apply_env_overrides(data)
    if not isinstance(data, dict):
        raise ValueError("home-edge profile must be a JSON object")
    if source == "synthetic_template" and _has_env_overrides():
        source = "environment_overrides"
    return HomeEdgeProfile.from_mapping(data, source=source)


def synthetic_profile_mapping() -> dict[str, Any]:
    return {
        "node_id": "home-edge-01",
        "hostname": HOME_EDGE_PUBLIC_PROFILE.hostname,
        "tailscale_ip": HOME_EDGE_PUBLIC_PROFILE.tailscale_ip,
        "controller": {
            "host": HOME_EDGE_PUBLIC_PROFILE.controller_host,
            "tailscale_ip": HOME_EDGE_PUBLIC_PROFILE.controller_tailscale_ip,
        },
        "ssh": {
            "target_user": HOME_EDGE_PUBLIC_PROFILE.target_user,
            "transport": "openssh_over_tailscale_ip",
            "identity_env": "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE",
            "known_hosts_env": "SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE",
        },
        "gateway": {
            "task_model": "typed_allowlisted_actions",
            "risk_lanes": ["read_only", "approved_mutation", "destructive_manual"],
        },
        "os": "linux",
        "primary_network": {"interface": "synthetic-lan", "gateway": "192.0.2.1"},
        "capabilities": [
            "system_administration",
            "network_diagnostics",
            "containers_and_services",
            "browser_and_desktop_diagnostics",
            "media_operations",
            "usb_hardware_inventory",
            "home_automation",
            "direct_routine_controls",
        ],
        "safety_boundaries": [
            "read_only_diagnostics_default",
            "strict_ssh_host_key_checking",
            "typed_allowlisted_actions_only",
            "no_raw_shell_from_issue_payload",
            "public_reports_aggregate_only",
        ],
    }


def _profile_path(path: str | Path | None) -> Path | None:
    if path is not None:
        return Path(path)
    env_path = os.environ.get(LOCAL_PROFILE_ENV, "").strip()
    if env_path:
        return Path(env_path)
    default_path = ROOT / DEFAULT_PROFILE_PATH
    return default_path if default_path.exists() else None


def _read_profile_mapping(profile_path: Path) -> dict[str, Any]:
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("home-edge profile must be a JSON object")
    return data


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(data))
    for field, env_name in ENV_OVERRIDES.items():
        value = os.environ.get(env_name, "").strip()
        if not value:
            continue
        if field == "target_user":
            result.setdefault("ssh", {})["target_user"] = value
        elif field == "controller_host":
            result.setdefault("controller", {})["host"] = value
        elif field == "controller_tailscale_ip":
            result.setdefault("controller", {})["tailscale_ip"] = value
        else:
            result[field] = value
    return result


def _has_env_overrides() -> bool:
    return any(os.environ.get(env_name, "").strip() for env_name in ENV_OVERRIDES.values())


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _strings(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return value
